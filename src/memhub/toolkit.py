"""Framework-neutral memory tools: the same search / propose / load-skill an agent gets from `MemoryMiddleware`, as plain
callables plus JSON-schema tool specs, so any agent stack (OpenAI Agents SDK, LlamaIndex, a hand-written loop, a FastAPI
endpoint, an MCP wrapper) can use memhub without LangChain.

    kit = MemoryToolkit(service, workspace_id="acme", user_id="cust-anna")
    kit.call("search_memory", {"query": "login loop"})        # -> JSON string, what a tool result should be
    tools = kit.tool_specs()                                  # -> OpenAI-style function specs (also fine for Anthropic/Mistral)
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from memhub.service import Actor, MemoryService, NotFound, ServiceError


def proposal_result(row: dict) -> dict[str, str]:
    """What a tool tells the model about a proposal: the stable memory id (not the version row id), the write
    event, the status - the same vocabulary as `memhub.MemoryEvent` - and the content, so the model can tell its
    drafts apart and correct one with `memory_id`."""
    return {"id": str(row["memory_id"]), "event": "PENDING", "status": row["status"], "content": row["content"]}


def proposal_id(memory_id: str | None) -> uuid.UUID | None:
    """The `memory_id` a model passed to correct a draft; a malformed one is an unknown memory."""
    if memory_id is None:
        return None
    try:
        return uuid.UUID(str(memory_id))
    except ValueError as exc:
        raise NotFound(f"no memory {memory_id}") from exc


PROPOSE_DESCRIPTION = ("Propose a new memory. It is saved as a candidate and needs review before it is used. "
                       "To correct a draft you proposed, pass its id as memory_id.")
PROPOSE_EXTRA_PARAMETERS = {
    "memory_id": {"type": "string", "description": "the id of your own pending draft, to correct it instead of adding one"},
    "user_requested": {"type": "boolean", "description": "true only when the user explicitly asks to save something a reviewer rejected before"},
}


_COMMON = {"entities", "tags"}  # every type has them; listing them would only hide the fields that matter


def _fields_hint(service: MemoryService) -> str:
    """Each type's required fields, then its optional ones, so the model fills `fields` right the first time."""
    parts = []
    for name in service.settings.types:
        if name not in service.registry:
            continue
        model_fields = service.registry.latest(name).model_fields
        required = [n for n, f in model_fields.items() if f.is_required()]
        optional = [n for n, f in model_fields.items() if not f.is_required() and n not in _COMMON]
        parts.append(f"{name}: {', '.join(required)}" + (f" (optional: {', '.join(optional)})" if optional else ""))
    return "the type's fields; required per type: " + "; ".join(parts)


def _view(row: dict) -> dict[str, Any]:
    observed = row["observed_at"]
    return {
        "id": str(row["id"]), "type": row["type"], "content": row["content"], "verified": row["verified"],
        "scope": row["scope"], "as_of": observed.date().isoformat() if hasattr(observed, "date") else str(observed)[:10],
        "inferred": row.get("assertion") == "inferred",
    }


class MemoryToolkit:
    def __init__(self, service: MemoryService, *, workspace_id: str, user_id: str | None, k: int = 5, actor: Actor | None = None,
                 run_id: str | None = None, agent_id: str | None = None):
        """`run_id` / `agent_id` (the app's run and agent) are stored on what `propose_memory` writes."""
        self.service, self.workspace_id, self.user_id, self.k = service, workspace_id, user_id, k
        self.actor = actor or Actor(id="agent")
        self.run_id, self.agent_id = run_id, agent_id

    def search_memory(self, query: str, type: str | None = None) -> list[dict[str, Any]]:
        rows = self.service.search(self.actor, query, workspace_id=self.workspace_id, user_id=self.user_id, k=self.k, type=type)
        return [_view(r) for r in rows]

    def propose_memory(self, type: str, fields: dict, evidence: list[dict] | None = None, scope: str = "user",
                       memory_id: str | None = None, user_requested: bool = False) -> dict[str, Any]:
        """Always a candidate: nothing an agent proposes is used until a person approves it."""
        try:
            row = self.service.propose(
                self.actor, type=type, scope=scope, fields=fields, evidence=evidence or [],
                workspace_id=self.workspace_id, user_id=self.user_id, run_id=self.run_id, agent_id=self.agent_id,
                memory_id=proposal_id(memory_id), user_requested=user_requested,
            )
        except (ServiceError, NotFound) as exc:
            return {"rejected": str(exc)}
        return proposal_result(row)

    def tool_specs(self) -> list[dict[str, Any]]:
        types = list(self.service.settings.types)
        return [
            {"type": "function", "function": {
                "name": "search_memory", "description": "Search the long-term memory (facts, preferences, past cases) for the current customer or workspace.",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"}, "type": {"type": "string", "enum": types, "description": "optional: only this kind"}},
                    "required": ["query"]}}},
            {"type": "function", "function": {
                "name": "propose_memory", "description": PROPOSE_DESCRIPTION,
                "parameters": {"type": "object", "properties": {
                    "type": {"type": "string", "enum": types}, "fields": {"type": "object", "description": _fields_hint(self.service)},
                    "evidence": {"type": "array", "items": {"type": "object"}}, "scope": {"type": "string", "enum": self.service.settings.scopes}, **PROPOSE_EXTRA_PARAMETERS},
                    "required": ["type", "fields"]}}},
        ]

    def call(self, name: str, arguments: dict[str, Any] | str) -> str:
        """Run a tool by name with the model's arguments (a dict or its JSON text); the result is a JSON string."""
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        if name not in ("search_memory", "propose_memory"):
            return json.dumps({"error": f"unknown tool {name!r}"})
        try:
            return json.dumps(getattr(self, name)(**args), default=str, ensure_ascii=False)
        except TypeError as exc:  # the model sent arguments the tool does not take
            return json.dumps({"error": str(exc)})
