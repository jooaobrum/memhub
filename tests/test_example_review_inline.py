"""`examples/review_inline` (issue #19, S2): inline review of an agent's proposed memory, built on public calls only.

Driven with a scripted chat model and an in-memory checkpointer, the way an app's chat loop drives it."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import memhub
from memhub import Memory
from memhub.config import TypeConfig
from tests.fakes import FakeEmbeddings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "review_inline"
IDS = dict(workspace_id="acme", user_id="anna", run_id="r")
REVIEWER = dict(workspace_id="acme", user_id="rev", run_id="r", roles=["workspace_admin"])
EVIDENCE = [{"message_id": "m1", "quote": "we moved login to Okta"}]


def _example():
    spec = importlib.util.spec_from_file_location("review_inline_agent", EXAMPLE / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Scripted(BaseChatModel):
    """Answers with scripted messages."""

    script: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


@pytest.fixture
def memory(settings) -> Memory:
    cfg = settings.model_copy(deep=True)
    cfg.types = {**cfg.types, "reading": TypeConfig(fields={"count": "int"})}
    m = Memory(db="memory" + "://", embedder=FakeEmbeddings(dims=16), config=cfg)
    m.init()
    return m


def _agent(memory, args: dict):
    model = Scripted(script=[
        AIMessage(content="", tool_calls=[{"name": "propose_memory", "args": args, "id": "call-1"}]),
        AIMessage(content="noted"),
    ])
    return _example().build_agent(memory, model, checkpointer=InMemorySaver(), **IDS)


class Run:
    """One thread of the agent: start it, answer its interrupts, collect its custom stream events."""

    def __init__(self, agent):
        self.agent, self.config, self.events = agent, {"configurable": {"thread_id": "t1"}}, []

    def _stream(self, payload) -> list:
        for mode, chunk in self.agent.stream(payload, self.config, stream_mode=["custom", "updates"]):
            if mode == "custom":
                self.events.append(chunk)
        return list(self.agent.get_state(self.config).interrupts)

    def start(self, text: str = "we moved login to Okta") -> list:
        return self._stream({"messages": [HumanMessage(text)]})

    def answer(self, interrupt, decision: dict) -> list:
        return self._stream(Command(resume={interrupt.id: decision}))


FACT = {"type": "fact", "fields": {"content": "Login uses Okta"}, "evidence": EVIDENCE, "scope": "user"}


def _rows(memory) -> list[dict]:
    return memory.get_all(**IDS)


# --- C27 -----------------------------------------------------------------------------------------------------------

def test_interrupt_describes_the_memory(memory):
    run = Run(_agent(memory, FACT))
    [pending] = run.start()
    assert pending.value["name"] == "propose_memory" and pending.value["tool_call_id"] == "call-1"
    description = pending.value["description"]
    for part in ("fact", "Login uses Okta", "user", "we moved login to Okta"):
        assert part in description
    assert _rows(memory) == [] and memory.queue(**REVIEWER) == []


# --- C28 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("decision, content", [
    ({"type": "approve"}, "Login uses Okta"),
    ({"type": "edit", "edited_action": {"name": "propose_memory",
                                        "args": {**FACT, "fields": {"content": "Login uses Okta SSO"}}}},
     "Login uses Okta SSO"),
], ids=["approve", "edit"])
def test_approve_or_edit_makes_it_active(memory, decision, content):
    run = Run(_agent(memory, FACT))
    [pending] = run.start()
    assert run.answer(pending, decision) == []
    [row] = _rows(memory)
    assert (row["content"], row["status"], row["verified"], row["reviewed_by"]) == (content, "active", True, "anna")
    assert memory.queue(**REVIEWER) == []


# --- C29 -----------------------------------------------------------------------------------------------------------

def test_invalid_edit_is_asked_again(memory):
    reading = {"type": "reading", "fields": {"content": "Pump read 3 times", "count": 3}, "evidence": EVIDENCE}
    run = Run(_agent(memory, reading))
    [first] = run.start()
    bad = {"type": "edit", "edited_action": {"name": "propose_memory",
                                             "args": {**reading, "fields": {"content": "Pump read", "count": "abc"}}}}
    [again] = run.answer(first, bad)
    assert again.value["tool_call_id"] == "call-1" and again.value["name"] == "propose_memory"
    assert "count" in again.value["description"] and "abc" in again.value["description"]
    assert _rows(memory) == [] and memory.queue(**REVIEWER) == []

    good = {"type": "edit", "edited_action": {"name": "propose_memory",
                                              "args": {**reading, "fields": {"content": "Pump read 4 times", "count": 4}}}}
    assert run.answer(again, good) == []
    [row] = _rows(memory)
    assert (row["content"], row["payload"]["count"], row["status"]) == ("Pump read 4 times", 4, "active")


# --- C30 -----------------------------------------------------------------------------------------------------------

def test_reject_writes_nothing(memory):
    run = Run(_agent(memory, FACT))
    [pending] = run.start()
    assert run.answer(pending, {"type": "reject"}) == []
    assert _rows(memory) == [] and memory.queue(**REVIEWER) == []


# --- C31 -----------------------------------------------------------------------------------------------------------

def test_stream_event(memory):
    run = Run(_agent(memory, FACT))
    [pending] = run.start()
    run.answer(pending, {"type": "approve"})
    [row] = _rows(memory)
    assert run.events == [{"type": "memory_proposed", "id": str(row["memory_id"]), "event": "ADD", "status": "active",
                           "content": "Login uses Okta", "memory_type": "fact"}]

    rejected = Run(_agent(memory, {**FACT, "fields": {"content": "Billing uses Adyen"}}))
    [pending] = rejected.start()
    rejected.answer(pending, {"type": "reject"})
    assert rejected.events == []


# --- C32 -----------------------------------------------------------------------------------------------------------

def test_public_imports_only():
    names = []
    for path in EXAMPLE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "memhub":
                assert node.module == "memhub", f"{path.name} imports {node.module}"
                names += [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("memhub."), f"{path.name} imports {alias.name}"
    assert names and set(names) <= set(memhub.__all__)
