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


def proposal_result(row: dict) -> dict[str, str]:
    """What a tool tells the model about a proposal: the stable memory id (not the version row id), the write
    event and the status - the same vocabulary as `memhub.MemoryEvent`."""
    return {"id": str(row["memory_id"]), "event": "PENDING", "status": row["status"]}


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
