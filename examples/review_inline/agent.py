"""Inline review of the memories an agent proposes, built on memhub's public API only.

The agent gets one tool, `propose_memory`. LangChain's human-in-the-loop middleware pauses every call to it, and
the person in the chat approves it, edits it or rejects it:

- approve, or an edit that passes the memory type's check: the memory is saved active and verified at once,
  through `Memory.propose(..., approve=True)`. A reviewer who may not approve that scope gets a candidate instead,
  which waits in `memhub queue`;
- an edit that fails the check: nothing is saved, and the same call is asked again with the error;
- reject: nothing is saved.

Each memory saved is also announced on the agent's stream as a `memory_proposed` custom event, so a UI can show
it without parsing what the model says.

    agent = build_agent(memory, model, checkpointer=InMemorySaver(), workspace_id="acme", user_id="anna", run_id="r1")
    for mode, chunk in agent.stream({"messages": [...]}, config, stream_mode=["custom", "updates"]): ...
    agent.stream(Command(resume={interrupt.id: {"type": "approve"}}), config, ...)   # answer the review

Needs `langchain>=1.4.6` (per-call interrupts). Run `python examples/review_inline/agent.py` for a terminal demo.
"""
from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from langgraph.types import interrupt

from memhub import Memory, ServiceError, ValidationError

DECISIONS = ["approve", "edit", "reject"]
REJECTED = "The reviewer rejected this memory. Do not propose it again unless the user asks for it."


def _render(memory: Memory, ids: dict, type: str, fields: dict) -> tuple[str, str | None]:
    """The content the memory would have, and the check's error if its fields are not valid."""
    try:
        return memory.check_fields(type, fields, **ids)["content"], None
    except ValidationError as exc:
        return str(fields.get("content", "")), str(exc)


def describe(memory: Memory, ids: dict, args: dict, error: str | None = None) -> str:
    """What the reviewer reads: type, content, scope, the quotes it rests on, and the error of a bad edit."""
    content, check = _render(memory, ids, args.get("type", ""), args.get("fields") or {})
    lines = [f"Save this {args.get('type')} memory ({args.get('scope', 'user')} scope)?", f"Content: {content}"]
    quotes = [e.get("quote") for e in args.get("evidence") or [] if e.get("quote")]
    lines += ["Evidence:", *(f'- "{q}"' for q in quotes)] if quotes else ["Evidence: none given"]
    if error or check:
        lines.append(f"Not valid, edit or reject it: {error or check}")
    return "\n".join(lines)


def build_agent(memory: Memory, model: Any, *, checkpointer: Any, workspace_id: str, user_id: str, run_id: str,
                roles: list[str] | None = None, system_prompt: str | None = None) -> Any:
    """An agent whose proposed memories the user reviews in the chat. `user_id` and `roles` are the reviewer's:
    their own memories (user scope) they may approve; workspace memories need an approving role."""
    ids = dict(workspace_id=workspace_id, user_id=user_id, run_id=run_id, roles=roles)

    @tool
    def propose_memory(type: str, fields: dict, runtime: ToolRuntime, evidence: list[dict] | None = None,
                       scope: str = "user") -> str:
        """Propose a long-term memory about the user or the workspace. The user reviews it before it is saved."""
        args = {"type": type, "fields": fields, "evidence": evidence or [], "scope": scope}
        while True:  # an edit the reviewer made may not pass the check: ask again until it does, or is rejected
            _, error = _render(memory, ids, args["type"], args["fields"])
            if error is None:
                break
            answer = interrupt({"type": "tool_approval", "tool_call_id": runtime.tool_call_id,
                                "name": "propose_memory", "args": args,
                                "description": describe(memory, ids, args, error)})
            if answer.get("type") == "reject":
                return REJECTED
            if answer.get("type") == "edit":
                args = {**args, **answer["edited_action"]["args"]}
        try:
            event = memory.propose(args["type"], args["fields"], evidence=args["evidence"], scope=args["scope"],
                                   approve=True, **ids)
        except ServiceError as exc:
            return json.dumps({"rejected": str(exc)})
        runtime.stream_writer({"type": "memory_proposed", "id": event.id, "event": event.event,
                               "status": event.status, "content": event.content, "memory_type": event.type})
        return json.dumps({"id": event.id, "event": event.event, "status": event.status, "content": event.content})

    review = HumanInTheLoopMiddleware(
        interrupt_on={"propose_memory": {
            "allowed_decisions": DECISIONS,
            "description": lambda call, state, runtime: describe(memory, ids, call["args"]),
        }},
        interrupt_mode="per_call",
    )
    # list the review before any tool-retry middleware, or a retried call asks the reviewer again
    return create_agent(model, tools=[propose_memory], middleware=[review], checkpointer=checkpointer,
                        system_prompt=system_prompt)


def main() -> None:  # pragma: no cover - a terminal demo with a real model
    import uuid

    from langchain.chat_models import init_chat_model
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    memory = Memory()  # memhub.yaml + MEMHUB_* from the working folder
    agent = build_agent(memory, init_chat_model("openai:gpt-4.1-mini"), checkpointer=InMemorySaver(),
                        workspace_id="demo", user_id="me", run_id=uuid.uuid4().hex,
                        system_prompt="When the user tells you something worth remembering, call propose_memory.")
    config = {"configurable": {"thread_id": "demo"}}
    payload: Any = None
    while True:
        payload = payload or {"messages": [{"role": "user", "content": input("you> ")}]}
        for mode, chunk in agent.stream(payload, config, stream_mode=["custom", "updates"]):
            if mode == "custom":
                print(f"[saved] {chunk['content']} ({chunk['status']})")
        pending = agent.get_state(config).interrupts
        if not pending:
            print("agent>", agent.get_state(config).values["messages"][-1].content)
            payload = None
            continue
        print(pending[0].value["description"])
        choice = input("approve / edit <json fields> / reject> ").strip()
        if choice.startswith("edit"):
            args = {**pending[0].value["args"], "fields": json.loads(choice[4:])}
            decision = {"type": "edit", "edited_action": {"name": "propose_memory", "args": args}}
        else:
            decision = {"type": "reject" if choice == "reject" else "approve"}
        payload = Command(resume={pending[0].id: decision})


if __name__ == "__main__":
    main()
