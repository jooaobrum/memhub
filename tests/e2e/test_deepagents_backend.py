"""#41 end to end: a deepagents agent keeps its memory in files under `/memories/`, and memhub decides what is kept.

Every scenario runs a real `create_deep_agent` with `CompositeBackend(default=StateBackend(), routes={"/memories/":
MemhubBackend(...)})`: the agent's own tools (`ls`, `read_file`, `grep`, `glob`, `write_file`, `edit_file`), the
backend, memhub, and the `memhub` CLI a reviewer uses. The two models are simulated: the agent's model chooses its
tool calls from what it just read (as a model would), and memhub's line check is scripted. SQLite (no Docker) and
Postgres (the Docker fixture).

    S1  reading: folders, README, a page, another user's folder
    S2  a personal line the user said is saved at once
    S3  a team line waits, a reviewer approves it with `memhub approve`, the agent reads it back
    S4  a team line of a type with `review: false` is saved at once
    S5  a line nobody said is a flag: never a memory, approve refuses it, reject dismisses it
    S6  edits keep the id: personal saved, team waits and supersedes on approve
    S7  removals: personal archived, team proposed for archiving and archived on approve
    S8  guards: a changed id, a header edit, delete, a new area file
    S9  a stale whole-file write is refused; the agent reads again and writes
    S10 grep and glob over the pages
    S11 a large area shows subject summaries; the agent opens one
    S12 what a reviewer rejected is not proposed again
    S13 a personal type that needs review waits, and says why
"""
from __future__ import annotations

import importlib.util
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
import yaml
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from typer.testing import CliRunner

from memhub import Memory, cli
from memhub.pipeline.extract import Candidate, Evidence
from memhub.storage.rules import area_link
from memhub.types import Area
from tests.fakes import FakeChatModel, FakeEmbeddings, extraction

ROOT = Path(__file__).resolve().parents[2]
CTX = {"team": "plant-7", "user": "anna"}
ANNA = dict(workspace_id="plant-7", user_id="anna", run_id="e2e")
NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()
runner = CliRunner()


# --- the simulated agent --------------------------------------------------------------------------------------


Step = Callable[[list], AIMessage]


class Agent(BaseChatModel):
    """The agent's model: each step sees the conversation so far and returns the next message, as a model would."""

    steps: list[Any]

    @property
    def _llm_type(self) -> str:
        return "simulated"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        step = self.steps.pop(0)
        return ChatResult(generations=[ChatGeneration(message=step(messages) if callable(step) else step)])


def call(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{uuid.uuid4().hex[:8]}"}])


def last_tool(messages: list) -> str:
    return next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))


def plain(tool_content: str) -> str:
    """A `read_file` result without deepagents' line-number gutter: the page as the agent would write it back."""
    return "\n".join(re.sub(r"^\s*\d+(?:\.\d+)?\t", "", ln) for ln in tool_content.splitlines()) + "\n"


def line_id(page: str, text: str) -> str:
    return re.search(rf"#([0-9a-f]{{8,}}) \[\w+\] .*{re.escape(text)}", page).group(1)


# --- the ledger: memhub.yaml, a Memory, and the CLI a reviewer uses ------------------------------------------


def candidate(content: str, quote: str, *, type: str = "fact", scope: str = "workspace", **fields) -> Candidate:
    return Candidate(type=type, scope=scope, fields={"content": content, **fields}, utility=5, applies_generally=True,
                     assertion="stated", evidence=[Evidence(message_id="m1", quote=quote, claim_source="user")])


@pytest.fixture(params=["sqlite", "postgres"])
def env(request, tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'e2e.db'}" if request.param == "sqlite" else request.getfixturevalue("pg_dsn")
    monkeypatch.setattr(cli, "build_embeddings", lambda c: FakeEmbeddings(16))
    path = tmp_path / "memhub.yaml"
    path.write_text(yaml.safe_dump({
        "project_prefix": f"e{uuid.uuid4().hex[:8]}", "database_url": url, "workspace_default": "plant-7",
        "scopes": ["user", "workspace"], "entity_types": ["machine"],
        "types": {
            "fact": {"class": "memhub.types:Fact"},
            "preference": {"class": "memhub.types:Preference", "keyed": True, "keys": {"seat": "seat"}},
            "symptom": {"class": "memhub.types:Fact", "review": False},
            "diagnosis": {"class": "memhub.types:Fact", "scopes": ["user"], "review": True},
        },
        "embeddings": {"provider": "openai", "model": "fake-embed", "api_key_env": "UNSET", "dims": 16},
    }))
    memory = Memory(db=url, llm=FakeChatModel([]), embedder=FakeEmbeddings(16), config=path)
    memory.init()
    spec = importlib.util.spec_from_file_location("e2e_memhub_backend",
                                                  ROOT / "examples" / "deepagents_backend" / "memhub_backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    backend = module.MemhubBackend(memory, workspace_from=lambda ctx: ctx["team"], user_from=lambda ctx: ctx["user"])
    e = SimpleNamespace(path=path, memory=memory, backend=backend)
    e.travel = _area(memory, "travel", "user")
    e.pumps = _area(memory, "pumps", "workspace")
    _row(memory, "Flies from CDG", e.travel, quote="I always fly from CDG")
    _row(memory, "Pumps hum at night", e.pumps, observed_at=NOW - timedelta(days=1))
    return e


def _area(m: Memory, key: str, scope: str, user: str = "anna") -> dict:
    payload = Area(content=f"{key.title()}: ", title=key.title(), description="", proposed=False,
                   key=key).model_dump(mode="json")
    with m.store.unit_of_work() as uow:
        return uow.memories.add_memory(
            type="area", schema_version=1, scope=scope, workspace_id="plant-7",
            user_id=user if scope == "user" else None, content=payload["content"], payload=payload, entities=[],
            embedding=FakeEmbeddings(16).embed_query(key), evidence=[], status="active", verified=True,
            created_by="seed")


def _row(m: Memory, content: str, area: dict, *, quote: str | None = None, status: str = "active",
         entities: list | None = None, observed_at: datetime | None = None, reviewed_by: str | None = "auto") -> dict:
    evidence = [{"message_id": "m1", "quote": quote, "claim_source": "user"}] if quote else [{"source": "manual"}]
    with m.store.unit_of_work() as uow:
        return uow.memories.add_memory(
            type="fact", schema_version=1, scope=area["scope"], workspace_id="plant-7", user_id=area["user_id"],
            content=content, payload={"content": content}, entities=entities or [],
            embedding=FakeEmbeddings(16).embed_query(content), evidence=evidence, status=status, verified=False,
            created_by="seed", links=[area_link(area["memory_id"])], observed_at=observed_at, reviewed_by=reviewed_by)


def run(e, user_says: str, *steps: Any, checks: list | None = None) -> list[str]:
    """One agent turn on a fresh thread: the user's message, then the steps; returns every tool result."""
    e.memory.llm.queue[:] = list(checks or [])
    agent = create_deep_agent(model=Agent(steps=[*steps, AIMessage(content="done")]),
                              backend=CompositeBackend(default=StateBackend(), routes={"/memories/": e.backend}))
    out = agent.invoke({"messages": [HumanMessage(user_says)]}, {"configurable": {"thread_id": uuid.uuid4().hex}},
                       context=CTX)
    assert not e.memory.llm.queue, "a scripted line check was not used"
    return [m.content for m in out["messages"] if isinstance(m, ToolMessage)]


def append(path: str, line: str) -> Step:
    """Read the page (the previous step), then write it back with one line added, as an agent appends a fact."""
    return lambda messages: call("write_file", file_path=path, content=plain(last_tool(messages)) + line + "\n")


def memhub_cli(e, *args: str) -> Any:
    return runner.invoke(cli.app, [*args, "--config", str(e.path)])


def queue(e) -> list[dict]:
    out = memhub_cli(e, "queue", "--ws", "plant-7")
    assert out.exit_code == 0, out.output
    return json.loads(out.output)


def memory_section(page: str) -> str:
    return page.split("## Memory", 1)[1].split("## Pending review", 1)[0]


ME, TEAM = "/memories/users/anna/travel.md", "/memories/team/plant-7/pumps.md"


# --- scenarios ------------------------------------------------------------------------------------------------


def test_s1_reading(env):
    root, folder, readme, page, other, missing = run(
        env, "what do you remember about me?",
        call("ls", path="/memories/"), call("ls", path="/memories/users/anna/"),
        call("read_file", file_path="/memories/users/anna/README.md"), call("read_file", file_path=ME),
        call("ls", path="/memories/users/bob/"), call("read_file", file_path="/memories/team/plant-9/pumps.md"))
    assert "/memories/team/" in root and "/memories/users/" in root
    assert "/memories/users/anna/README.md" in folder and "/memories/users/anna/travel.md" in folder
    assert "## Areas" in readme and "travel.md: Travel (1 memories)" in readme
    assert "## How to read this" in page and '[said] "I always fly from CDG"' in page
    assert "not found" in other and "not found" in missing


def test_s2_personal_line_saved(env):
    tools = run(env, "I always take the window seat on night trains",
                call("read_file", file_path=ME), append(ME, "Takes the window seat on night trains"),
                call("read_file", file_path=ME),
                checks=[extraction(candidate("Takes the window seat on night trains",
                                             "I always take the window seat on night trains", scope="user"))])
    [new] = [r for r in env.memory.get_all(**ANNA) if r["content"] == "Takes the window seat on night trains"]
    assert f"saved #{new['memory_id'].hex[:8]}: Takes the window seat on night trains" in tools[1]
    assert '[said] "I always take the window seat on night trains"' in memory_section(tools[2])


def test_s3_team_line_reviewed(env):
    tools = run(env, "our pumps fail above 40°C",
                call("read_file", file_path=TEAM),
                lambda m: call("edit_file", file_path=TEAM, old_string="## Memory\n",
                               new_string="## Memory\nPumps fail above 40°C\n"),
                call("read_file", file_path=TEAM),
                checks=[extraction(candidate("Pumps fail above 40°C", "our pumps fail above 40°C"))])
    assert "pending review: Pumps fail above 40°C" in tools[1]
    assert f"- [pending] Pumps fail above 40°C. Proposed {TODAY}." in tools[2]
    assert "40°C" not in memory_section(tools[2])
    [queued] = queue(env)
    assert (queued["content"], queued["status"], queued["scope"]) == ("Pumps fail above 40°C", "candidate", "workspace")
    assert memhub_cli(env, "approve", queued["id"]).exit_code == 0
    [page] = run(env, "what do we know about pumps?", call("read_file", file_path=TEAM))
    assert re.search(r"#[0-9a-f]{8} \[reviewed\] .*Pumps fail above 40°C", memory_section(page))
    assert "## Pending review" not in page and queue(env) == []


def test_s4_team_type_without_review(env):
    tools = run(env, "valve V2 sticks when it is cold",
                call("read_file", file_path=TEAM), append(TEAM, "Valve V2 sticks when cold"),
                checks=[extraction(candidate("Valve V2 sticks when cold", "valve V2 sticks when it is cold",
                                             type="symptom"))])
    assert re.search(r"saved #[0-9a-f]{8}: Valve V2 sticks when cold", tools[1])
    assert queue(env) == []


def test_s5_flag(env):
    tools = run(env, "thanks, that's all", call("read_file", file_path=TEAM),
                append(TEAM, "Team uses Amex for travel"), call("read_file", file_path=TEAM),
                checks=[extraction()])
    assert "flag: Team uses Amex for travel (no supporting quote)" in tools[1]
    assert "Amex" not in memory_section(tools[2])
    assert all("Amex" not in h["content"] for h in env.memory.search("Amex travel", **ANNA))
    [flag] = queue(env)
    assert (flag["content"], flag["status"]) == ("Team uses Amex for travel", "flag")
    out = memhub_cli(env, "approve", flag["id"])
    assert out.exit_code == 1 and "a flag is not a memory: reject it to dismiss it" in out.output
    assert memhub_cli(env, "reject", flag["id"]).exit_code == 0
    assert queue(env) == []


def test_s6_edits_keep_the_id(env):
    def edit(path: str, old: str, new: str) -> Step:
        return lambda messages: call("write_file", file_path=path,
                                     content=plain(last_tool(messages)).replace(old, new, 1))

    _row(env.memory, "Takes the 7am shuttle", env.travel, observed_at=NOW - timedelta(days=2))
    tools = run(env, "I take the 8am shuttle now",
                call("read_file", file_path=ME), edit(ME, "Takes the 7am shuttle", "Takes the 8am shuttle"),
                call("read_file", file_path=ME),
                checks=[extraction(candidate("Takes the 8am shuttle", "I take the 8am shuttle now", scope="user"))])
    old = line_id(plain(tools[0]), "Takes the 7am shuttle")
    assert f"saved #{old}: Takes the 8am shuttle" in tools[1]
    assert line_id(plain(tools[2]), "8am shuttle") == old and "7am" not in tools[2]

    tools = run(env, "the pumps hum at night and in the morning",
                call("read_file", file_path=TEAM),
                edit(TEAM, "Pumps hum at night", "Pumps hum at night and in the morning"),
                checks=[extraction(candidate("Pumps hum at night and in the morning",
                                             "the pumps hum at night and in the morning"))])
    team_id = line_id(plain(tools[0]), "Pumps hum at night")
    assert "pending review: Pumps hum at night and in the morning" in tools[1]
    [queued] = queue(env)
    assert queued["version"] == 2
    assert memhub_cli(env, "approve", queued["id"]).exit_code == 0
    [page] = run(env, "pumps?", call("read_file", file_path=TEAM))
    assert line_id(plain(page), "Pumps hum at night and in the morning") == team_id
    history = env.memory.history(queued["memory_id"], **ANNA)
    assert [(h["version"], h["status"]) for h in history] == [(1, "superseded"), (2, "active")]


def test_s7_removals(env):
    def drop(path: str, text: str) -> Step:
        return lambda messages: call("write_file", file_path=path, content="\n".join(
            ln for ln in plain(last_tool(messages)).splitlines() if text not in ln) + "\n")

    tools = run(env, "forget where I fly from", call("read_file", file_path=ME), drop(ME, "I always fly from CDG"),
                call("read_file", file_path=ME))
    assert f"archived #{line_id(plain(tools[0]), 'I always fly from CDG')}" in tools[1]
    assert "CDG" not in memory_section(tools[2])

    tools = run(env, "the night hum is fixed", call("read_file", file_path=TEAM), drop(TEAM, "Pumps hum at night"))
    team_id = line_id(plain(tools[0]), "Pumps hum at night")
    assert f"pending review: archive #{team_id}" in tools[1]
    [queued] = queue(env)
    assert memhub_cli(env, "approve", queued["id"]).exit_code == 0
    [page] = run(env, "pumps?", call("read_file", file_path=TEAM))
    assert "Pumps hum" not in memory_section(page)
    assert env.memory.get_all(type="fact", **ANNA) == []


def test_s8_guards(env):
    tools = run(env, "tidy up",
                call("read_file", file_path=ME),
                lambda m: call("write_file", file_path=ME, content=re.sub(
                    r"#[0-9a-f]{8} ", "#deadbeef ", plain(last_tool(m)), count=1)),
                call("read_file", file_path=ME),
                lambda m: call("write_file", file_path=ME,
                               content=plain(last_tool(m)).replace("# Memory: Travel (anna)", "# My trips")),
                call("write_file", file_path="/memories/users/anna/new.md", content="A brand new area\n"))
    assert "refused: line #deadbeef id changed" in tools[1]
    assert "I always fly from CDG" in memory_section(tools[2])
    assert tools[3].splitlines()[1:] == ["no change"]
    assert "areas come from memory.md" in tools[4]
    assert env.backend.delete(ME).error == "areas come from memory.md: edit lines in an existing page"
    assert sorted(r["content"] for r in env.memory.get_all(type="fact", **ANNA)) == ["Flies from CDG", "Pumps hum at night"]


def test_s9_stale_write(env):
    def meanwhile(messages):  # a reviewer's change lands between the agent's read and its write
        _row(env.memory, "Pumps need oil monthly", env.pumps)
        return call("write_file", file_path=TEAM, content=plain(last_tool(messages)) + "Pumps fail above 40°C\n")

    tools = run(env, "our pumps fail above 40°C",
                call("read_file", file_path=TEAM), meanwhile,
                call("read_file", file_path=TEAM), append(TEAM, "Pumps fail above 40°C"),
                checks=[extraction(candidate("Pumps fail above 40°C", "our pumps fail above 40°C"))])
    assert "page changed since you read it: read it again" in tools[1]
    assert "Pumps need oil monthly" in tools[2]
    assert "pending review: Pumps fail above 40°C" in tools[3]


def test_s10_grep_and_glob(env):
    found, lines_found, pages = run(env, "where do I fly from?", call("grep", pattern="CDG"),
                                    call("grep", pattern="CDG", output_mode="content"), call("glob", pattern="**/*.md"))
    assert found.strip() == "/memories/users/anna/travel.md"
    assert '"I always fly from CDG"' in lines_found
    for p in ("/memories/users/anna/README.md", "/memories/users/anna/travel.md", "/memories/team/plant-7/README.md",
              "/memories/team/plant-7/pumps.md"):
        assert p in pages


def test_s11_subject_summaries(env):
    for i in range(12):
        _row(env.memory, f"Pump P{i} note", env.pumps, entities=[{"type": "machine", "id": "P-line"}],
             observed_at=NOW - timedelta(minutes=i))
    for i in range(19):
        _row(env.memory, f"General note {i}", env.pumps, observed_at=NOW - timedelta(hours=1, minutes=i))
    page, subject = run(env, "pumps?", call("read_file", file_path=TEAM),
                        call("read_file", file_path="/memories/team/plant-7/pumps/p-line.md"))
    assert "#s_p-line (12 memories) P-line: " in page and "→ pumps/p-line.md" in page
    assert "Pump P3 note" not in page and "General note 3" in page
    assert len(re.findall(r"\[added\] Pump P\d+ note", subject)) == 12


def test_s12_rejected_repeat(env):
    _row(env.memory, "Pumps fail above 40°C", env.pumps, status="rejected", reviewed_by="lead")
    tools = run(env, "our pumps fail above 40°C",
                call("read_file", file_path=TEAM), append(TEAM, "Pumps fail above 40°C"),
                checks=[extraction(candidate("Pumps fail above 40°C", "our pumps fail above 40°C"))])
    assert "refused: a reviewer rejected this before" in tools[1]
    assert queue(env) == []


def test_s13_personal_type_needs_review(env):
    tools = run(env, "I have asthma", call("read_file", file_path=ME), append(ME, "Has asthma"),
                call("read_file", file_path=ME),
                checks=[extraction(candidate("Has asthma", "I have asthma", type="diagnosis", scope="user"))])
    assert "pending review: Has asthma (needs review)" in tools[1]
    assert f"- [pending] Has asthma. Proposed {TODAY}." in tools[2]
    [queued] = queue(env)
    assert (queued["scope"], queued["user_id"]) == ("user", "anna")
