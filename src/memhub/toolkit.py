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

from memhub.service import Actor, MemoryService, ServiceError


def _view(row: dict) -> dict[str, Any]:
    observed = row["observed_at"]
    return {
        "id": str(row["id"]), "type": row["type"], "content": row["content"], "verified": row["verified"],
        "scope": row["scope"], "as_of": observed.date().isoformat() if hasattr(observed, "date") else str(observed)[:10],
        "inferred": row.get("assertion") == "inferred",
    }


class MemoryToolkit:
    def __init__(self, service: MemoryService, *, workspace_id: str, user_id: str | None, k: int = 5, actor: Actor | None = None):
        self.service, self.workspace_id, self.user_id, self.k = service, workspace_id, user_id, k
        self.actor = actor or Actor(id="agent")

    def search_memory(self, query: str, type: str | None = None) -> list[dict[str, Any]]:
        rows = self.service.search(self.actor, query, workspace_id=self.workspace_id, user_id=self.user_id, k=self.k, type=type)
        return [_view(r) for r in rows]

    def propose_memory(self, type: str, fields: dict, evidence: list[dict] | None = None, scope: str = "user") -> dict[str, Any]:
        """Always a candidate: nothing an agent proposes is used until a person approves it."""
        try:
            row = self.service.propose(
                self.actor, type=type, scope=scope, fields=fields, evidence=evidence or [],
                workspace_id=self.workspace_id, user_id=self.user_id,
            )
        except ServiceError as exc:
            return {"rejected": str(exc)}
        return {"proposed": str(row["id"]), "status": "candidate"}

    def tool_specs(self) -> list[dict[str, Any]]:
        types = list(self.service.settings.types)
        return [
            {"type": "function", "function": {
                "name": "search_memory", "description": "Search the long-term memory (facts, preferences, past cases) for the current customer or workspace.",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"}, "type": {"type": "string", "enum": types, "description": "optional: only this kind"}},
                    "required": ["query"]}}},
            {"type": "function", "function": {
                "name": "propose_memory", "description": "Propose a new memory. It is saved as a candidate and needs review before it is used.",
                "parameters": {"type": "object", "properties": {
                    "type": {"type": "string", "enum": types}, "fields": {"type": "object", "description": "the type's fields"},
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
