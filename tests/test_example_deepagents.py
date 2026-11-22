"""`examples/deepagents_backend` (#41): `MemhubBackend` under deepagents' `/memories/`, public `memhub` names only.

The backend is driven directly where a check is about one protocol call, through `CompositeBackend` where the
routed path matters, and through a real `create_deep_agent` with a scripted chat model for the round trip."""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from typer.testing import CliRunner

import memhub
from memhub import cli
from tests.fakes import FakeChatModel, FakeEmbeddings, extraction
from tests.test_pages import IDS, add_area, add_row, all_rows, cand, cfg, cli_ledger, memory  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "deepagents_backend"
NOW = datetime.now(timezone.utc)
runner = CliRunner()


def _module():
    spec = importlib.util.spec_from_file_location("memhub_backend_example", EXAMPLE / "memhub_backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def backend(m, messages=(), **kw):
    return _module().MemhubBackend(m, workspace_from="w1", user_from="u1", messages_from=lambda: list(messages), **kw)


def paths(result) -> list[str]:
    return [e["path"] for e in result.entries]


@pytest.fixture
def ledger(cfg):
    m = memory(cfg)
    travel = add_area(m, "travel")
    add_row(m, "Flies from CDG", travel, quote="I always fly from CDG")
    add_row(m, "Likes aisles", travel, observed_at=NOW - timedelta(days=1))
    add_row(m, "Seat 3A", travel, observed_at=NOW - timedelta(days=2))
    add_area(m, "work")
    secret = add_area(m, "secret", user="u2")
    add_row(m, "Their secret", secret, created_by="u2")
    pumps = add_area(m, "pumps", scope="workspace")
    add_row(m, "Pumps hum", pumps)
    return m


# --- S1: reads ------------------------------------------------------------------------------------------------


def test_ls_root_shows_own_folders(ledger):
    """C1 (AC 1)."""
    b = backend(ledger)
    assert paths(b.ls("/")) == ["/team/", "/users/"]
    assert paths(b.ls("/team/")) == ["/team/w1/"]
    assert paths(b.ls("/users")) == ["/users/u1/"]
    assert paths(backend(ledger, team_folder="shared").ls("/")) == ["/shared/", "/users/"]
    assert all(e["is_dir"] for e in b.ls("/").entries)


def test_ls_folder_lists_areas_with_rows(cfg, ledger):
    """C2 (AC 2)."""
    assert paths(backend(ledger).ls("/users/u1/")) == ["/users/u1/README.md", "/users/u1/travel.md"]
    empty = memory(cfg)
    assert paths(backend(empty).ls("/team/w1/")) == ["/team/w1/README.md"]


def test_other_folders_not_found(ledger):
    """C3 (AC 3)."""
    b = backend(ledger)
    for path in ("/users/u2/", "/team/w2/", "/users/nobody/"):
        res = b.ls(path)
        assert (res.error, res.entries) == ("not found", None)
    for path in ("/users/u2/secret.md", "/team/w2/pumps.md", "/users/nobody/README.md", "/users/u1/nope.md"):
        res = b.read(path)
        assert (res.error, res.file_data) == ("not found", None)


def test_grep_is_literal(ledger):
    """C12 (AC 12)."""
    b = backend(ledger)
    [hit] = b.grep("CDG").matches
    assert hit["path"] == "/users/u1/travel.md"
    page = b.read("/users/u1/travel.md").file_data["content"].splitlines()
    assert page[hit["line"] - 1] == hit["text"] and "CDG" in hit["text"]
    assert b.grep("C.G").matches == []
    assert all(h["path"].startswith("/team/") for h in b.grep("#", path="/team/w1").matches)
    assert b.grep("#", path="/team/w1").matches


def test_glob_virtual_paths(cfg, ledger):
    """C13 (AC 13)."""
    for i in range(31):
        add_row(ledger, f"Flight {i}", next(a for a in _areas(ledger) if a["payload"]["key"] == "travel"),
                entities=[{"type": "machine", "id": "flights"}] if i < 12 else None)
    b = backend(ledger)
    found = sorted(e["path"] for e in b.glob("**/*.md").matches)
    assert found == ["/team/w1/README.md", "/team/w1/pumps.md", "/users/u1/README.md", "/users/u1/travel.md",
                     "/users/u1/travel/flights.md"]
    assert [e["path"] for e in b.glob("users/*/travel.md").matches] == ["/users/u1/travel.md"]


def _areas(m):
    with m.store.unit_of_work() as uow:
        return uow.memories.list_memories(type="area")


def test_read_window(ledger):
    """C14 (AC 14), and C15's backend half: `read` serves exactly `Memory.render`."""
    b = backend(ledger)
    full = ledger.render("travel", **IDS).splitlines()
    res = b.read("/users/u1/travel.md", offset=2, limit=3)
    assert res.file_data["content"].splitlines() == full[2:5]
    assert (res.start_line, res.end_line, res.total_lines, res.next_offset) == (3, 5, len(full), 5)
    assert b.read("/users/u1/travel.md", limit=0).no_lines_requested is True
    assert b.read("/users/u1/travel.md", offset=-4, limit=1).start_line == 1
    whole = b.read("/users/u1/travel.md").file_data["content"].splitlines()
    assert [ln for ln in whole if "rendered" not in ln] == [ln for ln in full if "rendered" not in ln]
    [download] = b.download_files(["/users/u1/travel.md"])
    assert download.error is None and download.content.decode("utf-8").splitlines()[0] == full[0]


# --- writes ---------------------------------------------------------------------------------------------------


def test_stale_write_and_fresh_edit(cfg):
    """C28 (AC 28), the backend's side."""
    m = memory(cfg)
    travel = add_area(m, "travel")
    add_row(m, "Flies from CDG", travel)
    b = backend(m)
    page = b.read("/users/u1/travel.md").file_data["content"]
    add_row(m, "Approved elsewhere", travel)
    res = b.write("/users/u1/travel.md", page.replace("Flies from CDG", "Flies from ORY"))
    assert res.error == "page changed since you read it: read it again"
    gone = re.search(r"^#([0-9a-f]+) \[\w+\] Flies from CDG", page, re.M).group(1)
    res = b.edit("/users/u1/travel.md", f"#{gone} [added] Flies from CDG ({NOW.date().isoformat()})\n", "")
    assert res.error is None and res.path.splitlines()[1:] == [f"archived #{gone}"]


def test_edit_old_string_errors(ledger):
    """C29 (AC 29)."""
    b = backend(ledger)
    before = len(all_rows(ledger))
    assert b.edit("/users/u1/travel.md", "Not on the page", "x").error.startswith("Error: String not found")
    assert "appears" in b.edit("/users/u1/travel.md", "[added]", "[x]").error
    assert len(all_rows(ledger)) == before


def test_refused_operations(ledger):
    """C30 (AC 30)."""
    b = backend(ledger)
    refused = "areas come from memory.md: edit lines in an existing page"
    before = len(all_rows(ledger))
    assert b.delete("/users/u1/travel.md").error == refused
    assert [r.error for r in b.upload_files([("/users/u1/x.md", b"hi")])] == [refused]
    assert b.write("/users/u1/brand_new.md", "Some fact\n").error == refused
    assert len(all_rows(ledger)) == before


def test_write_result_lines(cfg):
    """C34 (AC 34): through `CompositeBackend`, as the agent's tool sees it."""
    said = [{"role": "user", "content": "I take the train to Lyon"}]
    m = memory(cfg, extraction(cand("Takes the train to Lyon", quote="I take the train to Lyon", scope="user")),
               extraction())
    travel = add_area(m, "travel")
    add_row(m, "Flies from CDG", travel)
    add_row(m, "Likes aisles", travel, observed_at=NOW - timedelta(days=1))
    comp = CompositeBackend(default=StateBackend(), routes={"/memories/": backend(m, said)})
    page = comp.read("/memories/users/u1/travel.md").file_data["content"]
    cdg = re.search(r"^#([0-9a-f]+) \[\w+\] Flies from CDG", page, re.M).group(1)
    aisle = re.search(r"^#([0-9a-f]+) \[\w+\] Likes aisles", page, re.M).group(1)
    written = "\n".join(ln for ln in page.splitlines() if "Likes aisles" not in ln).replace(f"#{cdg} ", "#deadbeef ")
    written += "\nTakes the train to Lyon\nOwns a private jet\n"
    res = comp.write("/memories/users/u1/travel.md", written)
    assert res.error is None
    tool_message = f"Updated file {res.path}"  # what deepagents 0.7's write_file tool returns to the model
    [new] = [r for r in all_rows(m) if r["content"] == "Takes the train to Lyon"]
    assert tool_message.splitlines() == [
        "Updated file /memories/users/u1/travel.md",
        "refused: line #deadbeef id changed",
        f"saved #{new['memory_id'].hex[:8]}: Takes the train to Lyon",
        "flag: Owns a private jet (no supporting quote)",
        f"archived #{aisle}",
    ]


# --- S7 -------------------------------------------------------------------------------------------------------


def test_imports_public_only():
    """C35 (AC 35)."""
    names = set()
    for path in EXAMPLE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "memhub":
                assert node.module == "memhub", f"{path.name} imports {node.module}"
                names |= {a.name for a in node.names}
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith("memhub.") for a in node.names)
    assert names and names <= set(memhub.__all__)
    src = ROOT / "src" / "memhub"
    assert not [p for p in src.rglob("*.py") if re.search(r"^\s*(from|import) deepagents", p.read_text(), re.M)]


class Scripted(BaseChatModel):
    """Answers with scripted messages, one per model call."""

    script: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def _call(name: str, args: dict, n: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{n}"}])


def test_agent_round_trip(cfg, tmp_path, monkeypatch):
    """C36 (AC 36): a real deep agent reads a team page, writes a line the user said, a person approves it with
    `memhub approve`, and the agent's next read shows it as a memory line."""
    db = cli_ledger(tmp_path, monkeypatch, cfg)
    m = db.memory
    m.llm.queue.append(extraction(cand("Pumps fail above 40°C")))
    add_row(m, "Pumps hum", add_area(m, "pumps", scope="workspace"))
    page = "/memories/team/w1/pumps.md"
    model = Scripted(script=[
        _call("read_file", {"file_path": page}, 1),
        _call("edit_file", {"file_path": page, "old_string": "## Memory\n", "new_string": "## Memory\nPumps fail above 40°C\n"}, 2),
        AIMessage(content="noted"),
        _call("read_file", {"file_path": page}, 3),
        AIMessage(content="done"),
    ])
    mb = _module().MemhubBackend(m, workspace_from=lambda ctx: ctx["team"], user_from=lambda ctx: ctx["user"])
    agent = create_deep_agent(model=model, backend=CompositeBackend(default=StateBackend(), routes={"/memories/": mb}))
    config = {"configurable": {"thread_id": "t1"}}
    out = agent.invoke({"messages": [HumanMessage("our pumps fail above 40°C")]}, config,
                       context={"team": "w1", "user": "u1"})
    tools = [msg for msg in out["messages"] if isinstance(msg, ToolMessage)]
    assert "## How to read this" in tools[0].content and "Pumps hum" in tools[0].content
    assert "pending review: Pumps fail above 40°C" in tools[1].content, tools[1].content
    [queued] = m.queue(**IDS)
    result = runner.invoke(cli.app, ["approve", str(queued["id"]), "--config", str(db.path)])
    assert result.exit_code == 0, result.output
    out = agent.invoke({"messages": [HumanMessage("what do we know about pumps?")]}, config,
                       context={"team": "w1", "user": "u1"})
    last_read = [msg for msg in out["messages"] if isinstance(msg, ToolMessage)][-1].content
    body = last_read.split("## Memory", 1)[1]
    assert re.search(r"#[0-9a-f]{8} \[reviewed\] .*Pumps fail above 40°C", body), last_read


# --- the sweep ------------------------------------------------------------------------------------------------


def test_folder_template_guards(ledger):
    """C38."""
    same = backend(ledger, user_folder="x/{workspace_id}", team_folder="x/{workspace_id}")
    assert "two different paths" in same.ls("/").error
    sneaky = _module().MemhubBackend(ledger, workspace_from="w1", user_from="../u2", messages_from=list)
    assert "cannot name a folder" in sneaky.ls("/").error
    slashed = _module().MemhubBackend(ledger, workspace_from="w1/x", user_from="u1", messages_from=list)
    assert "cannot name a folder" in slashed.read("/users/u1/travel.md").error
