"""MemoryMiddleware: gives a LangChain agent read access to the ledger and a
propose-only write path.

`workspace_from` / `user_from` are called with `runtime.context` (whatever you
pass as `context=` to `agent.invoke`, typed by `create_agent(context_schema=...)`),
both in the hooks and in the tools. A plain string is used as-is.

The profile + skill index snapshot lives in agent state, so it is only fixed
for a whole thread when the agent has a checkpointer; without one, every
`invoke` starts from empty state and takes a fresh snapshot.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, NotRequired, Union

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from memhub.service import Actor, MemoryService, ServiceError

log = logging.getLogger(__name__)

Resolver = Union[str, Callable[[Any], str]]


class MemoryState(AgentState):
    memory_snapshot: NotRequired[str]  # profile + skill index, fixed per thread
    memory_turn: NotRequired[str]  # <memory> block for the current user turn
    memhub_injected: NotRequired[list[str]]  # `memory_id@version` of every injected item, across the thread


def _resolve(value: Resolver, context: Any) -> str:
    return value if isinstance(value, str) else value(context)


def _format(row: dict) -> str:
    sources = ", ".join(sorted({e.get("source", "?") for e in row["evidence"]}))
    verified = "verified" if row["verified"] else "unverified"
    when = row["observed_at"].date().isoformat() if hasattr(row["observed_at"], "date") else str(row["observed_at"])[:10]
    inferred = ", inferred" if row.get("assertion") == "inferred" else ""
    return f"[{row['id']}|{row['type']}|{verified}] {row['content']} (as of {when}{inferred}) ({sources})"

def _ref(row: dict) -> str:
    return f"{row['memory_id']}@{row['version']}"

def _write_trace(ids: list[str]) -> None:
    """Copy the injected list into the host's active MLflow trace, if there is one; never fails."""
    try:
        import mlflow

        if mlflow.get_current_active_span() is not None:
            mlflow.update_current_trace(metadata={"memhub_injected": json.dumps(ids)})
    except Exception:  # noqa: BLE001 - mlflow missing or no trace: nothing to record
        log.debug("could not write memhub_injected to the trace", exc_info=True)


def _memory_block(rows: list[dict]) -> str:
    return "<memory>\n" + "\n".join(_format(r) for r in rows) + "\n</memory>"


def _fit(lines: list[str], max_chars: int | None) -> str:
    """Keep whole entries within the budget (`lines` is newest first, so the newest win); never cut mid-text."""
    kept, used = [], 0
    for line in lines:
        used += len(line) + 1
        if max_chars is not None and used > max_chars:
            break
        kept.append(line)
    return "\n".join(kept)


class MemoryMiddleware(AgentMiddleware):
    state_schema = MemoryState

    def __init__(
        self,
        service: MemoryService,
        *,
        workspace_from: Resolver,
        user_from: Resolver,
        k: int = 5,
        actor: Actor | None = None,
    ) -> None:
        self.service = service
        self.workspace_from = workspace_from
        self.user_from = user_from
        self.k = k
        self.actor = actor or Actor(id="agent")
        self.tools = [self._search_memory_tool(), self._load_skill_tool(), self._propose_memory_tool()]

    def _ids(self, context: Any) -> tuple[str, str]:
        return _resolve(self.workspace_from, context), _resolve(self.user_from, context)

    def _visible(self, workspace_id: str, user_id: str, type_name: str) -> list[dict]:
        rows = self.service.list(self.actor, status="active", type=type_name, workspace_id=workspace_id)
        return [r for r in rows if not r["stale"] and (r["scope"] == "workspace" or r["user_id"] == user_id)]

    def _snapshot(self, workspace_id: str, user_id: str) -> tuple[str, list[str]]:
        parts, refs, about = [], [], []
        types = self.service.settings.types
        # Profile first, then the other `always` types (Preference): one "About the user" block, each type capped
        for name in sorted((n for n, c in types.items() if c.retrieval == "always"), key=lambda n: n != "profile"):
            rows = self._visible(workspace_id, user_id, name)
            text = _fit([_format(r) for r in rows], types[name].max_chars)
            if text:
                refs += [_ref(r) for r in rows if _format(r) in text]
                about.append(text)
        if about:
            parts.append("<about_the_user>\n" + "\n".join(about) + "\n</about_the_user>")
        for name, cfg in types.items():
            if cfg.retrieval == "index_then_load":
                lines = [
                    f"- {r['payload']['name']}: {r['payload']['description']}"
                    for r in self._visible(workspace_id, user_id, name)
                ]
                if lines:
                    parts.append(f"<{name}_index>\n" + "\n".join(lines) + f"\n</{name}_index>")
        return "\n".join(parts), refs

    def _page_block(self, page: dict) -> tuple[str, list[str]]:
        """The area page within `areas.page_max_chars`: title and summary always, then whole details newest first."""
        head = f"# {page['title']}"
        if page["summary"]:
            head += f"\nSummary (auto-summary): {page['summary']}"
        budget = self.service.settings.areas.page_max_chars - len(head)
        details = [(r, _format(r)) for r in page["details"]]
        kept = []
        for row, line in details:
            budget -= len(line) + 1
            if budget < 0:
                break
            kept.append((row, line))
        body = "\n".join([head, *(line for _, line in kept)])
        return f"<area_page>\n{body}\n</area_page>", [f"{page['memory_id']}@{page['version']}", *(_ref(r) for r, _ in kept)]

    def before_agent(self, state: MemoryState, runtime: Any) -> dict[str, Any] | None:
        workspace_id, user_id = self._ids(runtime.context)
        update: dict[str, Any] = {}
        if "memory_snapshot" not in state:
            update["memory_snapshot"], snapshot_refs = self._snapshot(workspace_id, user_id)
        else:
            snapshot_refs = []
        query = next((m.text for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), "")
        rows = self.service.search(self.actor, query, workspace_id=workspace_id, user_id=user_id, k=self.k)
        turn = [_memory_block(rows)] if rows else []
        page_refs: list[str] = []
        page = self.service.page_for_query(query, user_id, workspace_id=workspace_id) if query else None
        if page:
            block, page_refs = self._page_block(page)
            turn.insert(0, block)
        update["memory_turn"] = "\n".join(turn)
        # accumulates over the thread, first occurrence order, no duplicates
        injected = list(dict.fromkeys([*state.get("memhub_injected", []), *snapshot_refs, *page_refs, *map(_ref, rows)]))
        update["memhub_injected"] = injected
        _write_trace(injected)
        return update

    def wrap_model_call(self, request, handler):
        state = request.state
        extra = "\n".join(p for p in (state.get("memory_snapshot"), state.get("memory_turn")) if p)
        if not extra:
            return handler(request)
        # Injected into this request only; never saved in the message history.
        base = request.system_message.text + "\n\n" if request.system_message else ""
        return handler(request.override(system_message=SystemMessage(content=base + extra)))

    def _search_memory_tool(self):
        @tool
        def search_memory(query: str, runtime: ToolRuntime, type: str | None = None) -> str:
            """Search long-term memory, optionally restricted to one memory type."""
            workspace_id, user_id = self._ids(runtime.context)
            rows = self.service.search(
                self.actor, query, workspace_id=workspace_id, user_id=user_id, k=self.k, type=type
            )
            return _memory_block(rows) if rows else "no matching memories"

        return search_memory

    def _load_skill_tool(self):
        @tool
        def load_skill(name: str, runtime: ToolRuntime) -> str:
            """Return the full body of the skill with this name (see the skill index)."""
            workspace_id, user_id = self._ids(runtime.context)
            for r in self._visible(workspace_id, user_id, "skill"):
                if r["payload"]["name"] == name:
                    return r["payload"]["body"]
            return f"no active skill named {name!r}"

        return load_skill

    def _propose_memory_tool(self):
        @tool
        def propose_memory(
            type: str, fields: dict, evidence: list[dict], runtime: ToolRuntime, scope: str = "user"
        ) -> str:
            """Propose a new memory. It is saved as a candidate and needs review before it is used."""
            workspace_id, user_id = self._ids(runtime.context)
            try:
                row = self.service.propose(
                    self.actor,
                    type=type,
                    scope=scope,
                    fields=fields,
                    evidence=evidence,
                    workspace_id=workspace_id,
                    user_id=user_id,
                )
            except ServiceError as exc:
                return f"rejected: {exc}"
            return f"proposed as candidate {row['id']}"

        return propose_memory
