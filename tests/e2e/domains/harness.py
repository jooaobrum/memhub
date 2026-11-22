"""The harness of the domain scenarios (`tests/e2e/domains/test_*.py`): five customer projects, ten scenarios each.

Each scenario starts from nothing, as a customer does: a new project folder holding the project's own `memory.md`
(types, areas, subjects, review rule, written in the documented short forms), a `memhub.yaml` that names it and a
`connect: "<domain>_app:build"` function returning the app's own database, embedder and chat model, and a new
database (a SQLite file, or a new table prefix on the Postgres of the Docker fixture). The scenario then uses every
surface a customer has: `Memory` in Python, the `memhub` CLI on the same yaml, `MemoryToolkit`, `MemoryMiddleware`
on a LangChain agent, the MCP server, and a deepagents agent over `MemhubBackend`.

The models are simulated, so a scenario is exact and runs offline:

- `Model` is memhub's chat model. Each extraction call (a push, an ingest slice, a page line check) takes the next
  scripted answer from `model.say(...)`, or finds nothing; the reconcile judge answers `unrelated` unless a verdict is
  scripted with `model.judge(...)`. A scenario ends with `model.done()`: every scripted answer was used.
- `Agent` is an agent's model: it returns the scripted tool calls one by one, each built from the conversation so far.
- `FakeEmbeddings(64)` is a bag-of-words embedding: shared words give a similar vector, the same text the same one.

Quotes are real: a candidate's quote must be found word for word in a user message of the conversation, or memhub
drops it as `ungrounded`, exactly as with a real model.
"""
from __future__ import annotations

import json
import re
import textwrap
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from typer.testing import CliRunner

from memhub import Memory, cli
from memhub.pipeline.extract import AreaRef, Candidate, Evidence, Extraction
from memhub.pipeline.reconcile import Verdict
from tests.fakes import FakeAIMessage, FakeEmbeddings

ROOT = Path(__file__).resolve().parents[3]
DIMS = 64
NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()
runner = CliRunner()

CURRENT: dict[str, Any] = {}  # what the project's `connect:` function returns: set by `project()`
LAST: list["Project"] = []  # the scenario's project, for the opt-in dump (`E2E_DUMP_DIR`, conftest.py)


def connect() -> dict[str, Any]:
    """The body of every domain's `connect:` function: the scenario's database, embedder and chat model."""
    return dict(CURRENT)


# --- memhub's chat model ----------------------------------------------------------------------------------------


class Model:
    """memhub's chat model in a scenario: scripted extractions and judge verdicts, nothing else."""

    def __init__(self) -> None:
        self.extractions: list[Extraction] = []
        self.terms: list[Extraction] = []
        self.verdicts: list[Verdict] = []
        self.asked: dict[str, int] = {}

    def say(self, *candidates: Candidate) -> "Model":
        """The next extraction call (a push slice, an ingest slice or a page line check) returns these."""
        self.extractions.append(Extraction(candidates=list(candidates)))
        return self

    def judge(self, verdict: str, *, content: str | None = None, about: int | None = None) -> "Model":
        self.verdicts.append(Verdict(verdict=verdict, content=content, about=about))
        return self

    def done(self) -> None:
        assert not self.extractions, f"{len(self.extractions)} scripted extraction(s) never asked for"
        assert not self.verdicts, f"{len(self.verdicts)} scripted verdict(s) never asked for"

    def with_structured_output(self, schema: Any, *, include_raw: bool = False, **_kw: Any) -> Any:
        model = self

        class _Run:
            def invoke(self, *_a: Any, **_k: Any) -> Any:
                parsed = model._answer(schema)
                return {"raw": FakeAIMessage(), "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return _Run()

    def invoke(self, *_a: Any, **_k: Any) -> Any:
        return FakeAIMessage(content="")

    def _answer(self, schema: Any) -> Any:
        name = schema.__name__
        self.asked[name] = self.asked.get(name, 0) + 1
        if name == "Extraction":
            variants = set(re.findall(r"Candidate_(\w+)", repr(schema.model_fields["candidates"].annotation)))
            if variants == {"term"}:  # the glossary call of a project that keeps `term`
                return self.terms.pop(0) if self.terms else Extraction(candidates=[])
            return self.extractions.pop(0) if self.extractions else Extraction(candidates=[])
        if name == "Verdict":
            return self.verdicts.pop(0) if self.verdicts else Verdict(verdict="unrelated")
        if name == "Summary":
            return schema(summary="A summary.")
        if name == "_IsCorrection":
            return schema(is_correction=False)
        raise AssertionError(f"memhub asked the model an unscripted {name}")


def cand(type: str, fields: dict[str, Any] | str, quote: str, *, scope: str = "user", area: str | None = None,
         entities: list[dict] | None = None, durability: str = "ongoing", assertion: str = "stated",
         valid_until: datetime | None = None, utility: int = 5, by: str = "user") -> Candidate:
    """A candidate as a model returns it. `fields` as a string is `{"content": ...}`; `quote` is the user's words."""
    fields = {"content": fields} if isinstance(fields, str) else dict(fields)
    # the extraction schema asks every type for `content`; for a template type memhub then writes it from the fields
    fields.setdefault("content", " ".join(str(v) for v in fields.values() if isinstance(v, (str, int, float))))
    if entities is not None:
        fields["entities"] = entities
    return Candidate(type=type, scope=scope, fields=fields, utility=utility, applies_generally=True,
                     durability=durability, assertion=assertion, valid_until=valid_until,
                     areas=[AreaRef(existing=area)] if area else [],
                     evidence=[Evidence(message_id="m1", quote=quote, claim_source=by)])


# --- an agent's model -------------------------------------------------------------------------------------------


Step = Callable[[list], AIMessage]


class Agent(BaseChatModel):
    """An agent's model: each step sees the conversation so far and returns the next message, as a model would."""

    steps: list[Any]
    seen: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "simulated"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        step = self.steps.pop(0)
        return ChatResult(generations=[ChatGeneration(message=step(messages) if callable(step) else step)])


def call(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{uuid.uuid4().hex[:8]}"}])


def last_tool(messages: list) -> str:
    return next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))


def plain(tool_content: str) -> str:
    """A `read_file` result without deepagents' line-number gutter: the page as the agent would write it back."""
    return "\n".join(re.sub(r"^\s*\d+(?:\.\d+)?\t", "", ln) for ln in tool_content.splitlines()) + "\n"


def memory_section(page: str) -> str:
    return page.split("## Memory", 1)[1].split("## Pending review", 1)[0]


def line_id(page: str, text: str) -> str:
    return re.search(rf"#([0-9a-f]{{8,}}) \[\w+\] .*{re.escape(text)}", page).group(1)


# --- the project ------------------------------------------------------------------------------------------------


@dataclass
class Project:
    """One customer project: its folder, its `Memory`, its models, and the CLI on the same yaml."""

    folder: Path
    yaml_path: Path
    memory: Memory
    model: Model
    workspace: str
    backend_kind: str
    embedder: FakeEmbeddings = field(default_factory=lambda: FakeEmbeddings(DIMS))
    memory_md: str = ""
    log: list[dict] = field(default_factory=list)  # what happened, in order: pushes, agent turns, CLI commands

    def ids(self, user: str, *, roles: list[str] | None = None, run: str | None = None,
            workspace: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = dict(workspace_id=workspace or self.workspace, user_id=user, run_id=run or f"run-{user}")
        if roles is not None:
            out["roles"] = roles
        return out

    def as_(self, user: str, **kw: Any) -> Any:
        """`memory.scope(...)` for one user."""
        return self.memory.scope(**self.ids(user, **kw))

    def admin(self, user: str = "lead") -> Any:
        return self.as_(user, roles=["workspace_admin"])

    def push(self, user: str, said: list[str] | list[dict], *candidates: Candidate, thread: str | None = None,
             entities: list[dict] | None = None, **kw: Any) -> Any:
        """A conversation pushed with `add(infer=True)`; the model extracts `candidates` from it. A list of strings
        alternates user and assistant turns, starting with the user."""
        messages = said if said and isinstance(said[0], dict) else [
            {"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(said)]
        if candidates:
            self.model.say(*candidates)
        out = self.memory.add(messages, infer=True, thread_id=thread, entities=entities, **self.ids(user, **kw))
        self.log.append({"kind": "push", "user": user, "thread": thread, "entities": entities, "messages": messages,
                         "results": [e.model_dump() for e in out.results],
                         "dropped": [d.model_dump() for d in out.dropped]})
        return out

    def cli(self, *args: str, ok: bool = True, env: dict[str, str] | None = None, config: bool = True) -> Any:
        """`memhub <args> -c memhub.yaml`; the parsed JSON it prints (or the raw output when it is not JSON)."""
        out = runner.invoke(cli.app, [*args, *(["--config", str(self.yaml_path)] if config else [])], env=env)
        self.log.append({"kind": "cli", "args": list(args), "exit_code": out.exit_code, "output": out.output[:4000]})
        if ok:
            assert out.exit_code == 0, f"memhub {' '.join(args)} -> {out.exit_code}\n{out.output}"
        else:
            assert out.exit_code != 0, f"memhub {' '.join(args)} should have failed\n{out.output}"
            return out.output
        try:
            return json.loads(out.stdout)
        except json.JSONDecodeError:
            return out.stdout

    def contents(self, rows: list[dict]) -> list[str]:
        return [r["content"] for r in rows]


def project(request: Any, tmp_path: Path, monkeypatch: Any, *, name: str, memory_md: str,
            yaml_extra: dict[str, Any] | None = None, workspace: str) -> Project:
    """A new project folder and database for one scenario (`request.param`: `sqlite` or `postgres`)."""
    kind = request.param
    folder = tmp_path / name
    folder.mkdir()
    monkeypatch.chdir(folder)
    url = f"sqlite:///{folder / 'memhub.db'}" if kind == "sqlite" else request.getfixturevalue("pg_dsn")
    model, embedder = Model(), FakeEmbeddings(DIMS)
    CURRENT.clear()
    CURRENT.update(db=url, embedder=embedder, llm=model, judge=model)
    module = f"{name}_app"
    (folder / f"{module}.py").write_text(textwrap.dedent("""\
        from tests.e2e.domains.harness import connect


        def build():
            return connect()
        """))
    (folder / "memory.md").write_text(textwrap.dedent(memory_md).lstrip())
    config = {"project": f"{name}_{uuid.uuid4().hex[:6]}", "connect": f"{module}:build", "memory": "./memory.md",
              "workspace_default": workspace, **(yaml_extra or {})}
    path = folder / "memhub.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    memory = Memory(config=path)
    memory.init()
    LAST[:] = [Project(folder=folder, yaml_path=path, memory=memory, model=model, workspace=workspace,
                       backend_kind=kind, embedder=embedder, memory_md=textwrap.dedent(memory_md).lstrip())]
    return LAST[0]


def deepagents_backend(p: Project) -> Any:
    """The example `MemhubBackend` (examples/deepagents_backend) on the project's `Memory`."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("e2e_domain_backend",
                                                  ROOT / "examples" / "deepagents_backend" / "memhub_backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MemhubBackend(p.memory, workspace_from=lambda ctx: ctx["team"], user_from=lambda ctx: ctx["user"])


def deep_turn(p: Project, backend: Any, user: str, says: str, *steps: Any) -> list[str]:
    """One deepagents turn on a fresh thread: the user's message, then the agent's tool calls; every tool result."""
    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend, StateBackend
    from langchain_core.messages import HumanMessage

    agent = create_deep_agent(model=Agent(steps=[*steps, AIMessage(content="done")]),
                              backend=CompositeBackend(default=StateBackend(), routes={"/memories/": backend}))
    out = agent.invoke({"messages": [HumanMessage(says)]}, {"configurable": {"thread_id": uuid.uuid4().hex}},
                       context={"team": p.workspace, "user": user})
    calls = [tc for m in out["messages"] if isinstance(m, AIMessage) for tc in m.tool_calls]
    results = [m.content for m in out["messages"] if isinstance(m, ToolMessage)]
    p.log.append({"kind": "deepagents", "user": user, "says": says,
                  "steps": [{"tool": c["name"], "args": c["args"], "result": r} for c, r in zip(calls, results)]})
    return results


def append(path: str, line: str) -> Step:
    """Write back the page the previous step read, with one line added at the end, as an agent appends a fact."""
    return lambda messages: call("write_file", file_path=path, content=plain(last_tool(messages)) + line + "\n")


def replace(path: str, old: str, new: str) -> Step:
    return lambda messages: call("write_file", file_path=path, content=plain(last_tool(messages)).replace(old, new, 1))


def drop_line(path: str, text: str) -> Step:
    return lambda messages: call("write_file", file_path=path, content="\n".join(
        ln for ln in plain(last_tool(messages)).splitlines() if text not in ln) + "\n")
