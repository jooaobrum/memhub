"""Framework-neutral memory tools: the same search / propose / load-skill an agent gets from `MemoryMiddleware`, as plain
callables plus JSON-schema tool specs, so any agent stack (OpenAI Agents SDK, LlamaIndex, a hand-written loop, a FastAPI
endpoint, an MCP wrapper) can use memhub without LangChain.

    kit = MemoryToolkit(service, workspace_id="acme", user_id="cust-anna")
    kit.call("search_memory", {"query": "login loop"})        # -> JSON string, what a tool result should be
    tools = kit.tool_specs()                                  # -> OpenAI-style function specs (also fine for Anthropic/Mistral)
"""
from __future__ import annotations

import json
from typing import Any

from memhub.filters import HINT as FILTERS_HINT
from memhub.filters import parse as parse_filters
from memhub.service import Actor, MemoryService, ServiceError, ValidationError


def proposal_result(row: dict) -> dict[str, str]:
    """What a tool tells the model about a proposal: the stable memory id (not the version row id), the write
    event and the status - the same vocabulary as `memhub.MemoryEvent`."""
    return {"id": str(row["memory_id"]), "event": "PENDING", "status": row["status"]}


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

    def search_memory(self, query: str, type: str | None = None, filters: dict | str | None = None) -> Any:
        """`filters`: the `memhub.filters` grammar (an object, or its JSON text); a bad one comes back as `{"error"}`."""
        if filters is None:
            rows = self.service.search(self.actor, query, workspace_id=self.workspace_id, user_id=self.user_id, k=self.k, type=type)
            return [_view(r) for r in rows]
        try:
            flt = parse_filters(filters, declared=self.service.settings.declared_entity_types(),
                                types=[type] if type else None)
        except ValidationError as exc:
            return {"error": str(exc)}
        rows = self.service.search(self.actor, query, workspace_id=self.workspace_id, user_id=self.user_id, k=self.k,
                                   filter=flt)
        return [_view(r) for r in rows]

    def propose_memory(self, type: str, fields: dict, evidence: list[dict] | None = None, scope: str = "user") -> dict[str, Any]:
        """Always a candidate: nothing an agent proposes is used until a person approves it."""
        try:
            row = self.service.propose(
                self.actor, type=type, scope=scope, fields=fields, evidence=evidence or [],
                workspace_id=self.workspace_id, user_id=self.user_id, run_id=self.run_id, agent_id=self.agent_id,
            )
        except ServiceError as exc:
            return {"rejected": str(exc)}
        return proposal_result(row)

    def tool_specs(self) -> list[dict[str, Any]]:
        types = list(self.service.settings.types)
        return [
            {"type": "function", "function": {
                "name": "search_memory", "description": "Search the long-term memory (facts, preferences, past cases) for the current customer or workspace.",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"}, "type": {"type": "string", "enum": types, "description": "optional: only this kind"},
                    "filters": {"type": "object", "description": FILTERS_HINT}},
                    "required": ["query"]}}},
            {"type": "function", "function": {
                "name": "propose_memory", "description": "Propose a new memory. It is saved as a candidate and needs review before it is used.",
                "parameters": {"type": "object", "properties": {
                    "type": {"type": "string", "enum": types}, "fields": {"type": "object", "description": _fields_hint(self.service)},
                    "evidence": {"type": "array", "items": {"type": "object"}}, "scope": {"type": "string", "enum": self.service.settings.scopes}},
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
