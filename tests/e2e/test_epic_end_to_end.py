"""The whole epic in one run, as an app and its team would use it - SQLite, fake models, no Docker, no Postgres.

1. `memhub init --db sqlite:///...` writes memhub.yaml and creates the ledger (PR 2, PR 4) at schema version 2 (PR 6).
2. The app builds `Memory()` from that yaml (PR 1, PR 2) and pushes a conversation with `add(..., infer=True)` (PR 5):
   the user's memory is active at once, the workspace one waits for review.
3. A reviewer sees it in `memhub queue` and approves it from the CLI.
4. Another user of the workspace finds it with `search`; the first user's private memory stays private (PR 1).
5. Past Claude Code sessions are ingested with `memhub ingest --source claude_code` (PR 7) and approved.
6. `memhub mcp`, started as Claude Code starts it, answers `search_memory` with both shared memories and stores a
   `propose_memory` as a candidate (PR 7).
7. `memhub doctor` and `memhub migrate` agree the ledger is current (PR 6); `delete_all` erases a user (PR 1).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import psycopg
import pytest
import yaml
from mcp import Client, StdioServerParameters
from typer.testing import CliRunner

from memhub import Memory, cli
from memhub.mcp_server import folder_workspace
from memhub.pipeline.extract import Candidate, Evidence
from memhub.pipeline.push import to_interactions, to_messages
from tests.e2e import pr7_app
from tests.fakes import FakeChatModel, FakeEmbeddings, extraction

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()

SUPPORT_MEMORY = Path(__file__).resolve().parents[2] / "examples" / "support" / "memory.md"  # the support project's types


@pytest.fixture
def no_postgres(monkeypatch):
    def refuse(*_a, **_kw):
        raise AssertionError("the SQLite run must not open a Postgres connection")

    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(lambda cls, *a, **kw: refuse()))


def _cand(content: str, quote: str, mid: str, scope: str, type: str = "fact", **fields) -> Candidate:
    return Candidate(type=type, scope=scope, fields={"content": content, **fields}, utility=5, applies_generally=True,
                     assertion="stated", evidence=[Evidence(message_id=mid, quote=quote, claim_source="user")])


def _cli(*args: str) -> object:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, (args, result.output)
    return result


def test_the_whole_epic_on_sqlite(no_postgres, tmp_path, monkeypatch):
    project = (tmp_path / "plant app").resolve()
    project.mkdir()
    home = tmp_path / "home"
    workspace = folder_workspace(project)
    db = f"sqlite:///{project / 'memory.db'}"
    env = {"HOME": str(home), "MEMHUB_USER": "anna", "PR7_DB": db, "MEMHUB_CLI_ROLES": "workspace_admin"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(project)

    # 1. init: memhub.yaml with the app's own connect function, the ledger on SQLite at the current schema version
    (project / "memory.md").write_text(SUPPORT_MEMORY.read_text())
    _cli("init", "--connect", "tests.e2e.pr7_app:build", "--memory", "./memory.md", "--project", "plant")
    config = yaml.safe_load((project / "memhub.yaml").read_text())
    assert config["connect"] == "tests.e2e.pr7_app:build" and config["memory"] == "./memory.md"
    assert _cli("migrate").output.strip() == "already at schema version 2"

    # 2. the app pushes a conversation; ids are bound once with scope()
    conversation = [
        {"role": "user", "content": "I prefer answers in Portuguese"},
        {"role": "assistant", "content": "Combinado."},
        {"role": "user", "content": "Pump P4 cavitates when the suction valve is under half open"},
        {"role": "assistant", "content": "Noted: open the suction valve fully first."},
    ]
    ids = [m.message_id for m in to_interactions(to_messages(conversation), workspace_id=workspace, user_id="anna",
                                                 thread_id="trace-1", trace_id="trace-1", now=None)]
    model = FakeChatModel([extraction(
        _cand("Prefers answers in Portuguese", "I prefer answers in Portuguese", ids[0], "user",
              type="preference", key="language"),  # the support project's user-scope type
        _cand("Pump P4 cavitates below half-open suction", "Pump P4 cavitates when the suction valve is under half open",
              ids[2], "workspace"),
    )], unrelated_by_default=True)
    m = Memory(llm=model)  # db and embedder from memhub.yaml's connect function
    anna = m.scope(workspace_id=workspace, user_id="anna", run_id="trace-1")
    pushed = anna.add(conversation, infer=True)
    assert [(e.event, e.status, e.scope) for e in pushed.results] == [("ADD", "active", "user"),
                                                                     ("PENDING", "candidate", "workspace")]
    assert anna.add(conversation, infer=True).results == []  # the same conversation again: nothing new, no model call
    pump = pushed.results[1]

    # 3. a reviewer approves it from the CLI
    [pending] = json.loads(_cli("queue", "--ws", workspace).stdout)
    assert str(pending["memory_id"]) == pump.id
    assert json.loads(_cli("approve", pending["id"]).stdout)["status"] == "active"

    # 4. another user finds the shared memory, never anna's private one
    bob = m.scope(workspace_id=workspace, user_id="bob", run_id="trace-2")
    assert bob.search("pump P4 suction valve")[0]["content"] == pump.content
    assert "Prefers answers in Portuguese" not in [r["content"] for r in bob.search("answers in Portuguese")]
    assert anna.search("answers in Portuguese")[0]["content"] == "Prefers answers in Portuguese"

    # 5. past Claude Code sessions of this project become a shared, reviewed memory
    sessions = home / ".claude" / "projects" / workspace
    sessions.mkdir(parents=True)
    rule = "Integration tests need the pgvector container from docker compose"
    (sessions / "s-1.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "sessionId": "s-1", "uuid": "u1", "timestamp": "2026-11-01T10:00:00Z",
         "message": {"role": "user", "content": f"{rule}, remember that"}},
        {"type": "assistant", "sessionId": "s-1", "uuid": "a1", "timestamp": "2026-11-01T10:01:00Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Noted."}]}},
    ]) + "\n")
    monkeypatch.setattr(pr7_app, "LLM", FakeChatModel([extraction(_cand(rule, rule, "u1", "workspace")), extraction()],
                                                      unrelated_by_default=True))
    assert json.loads(_cli("ingest", "--source", "claude_code").stdout)["created"] == 1
    [from_session] = json.loads(_cli("queue", "--ws", workspace).stdout)
    _cli("approve", from_session["id"])

    # 6. Claude Code's MCP server, started in the project's folder, sees both and proposes a candidate
    server = StdioServerParameters(command=sys.executable, args=["-m", "memhub.cli", "mcp"], cwd=str(project),
                                   env={**os.environ, **env, "PYTHONPATH": str(ROOT)})

    async def session():
        async with Client(server) as client:
            pumps = await client.call_tool("search_memory", {"query": "pump P4 suction valve"})
            tests = await client.call_tool("search_memory", {"query": "integration tests pgvector docker"})
            proposal = await client.call_tool("propose_memory", {"type": "fact", "scope": "workspace",
                                                                 "fields": {"content": "CI runs on every push"}})
            return pumps, tests, proposal

    pumps, tests, proposal = anyio.run(session)
    assert json.loads(pumps.content[0].text)[0]["content"] == pump.content
    assert json.loads(tests.content[0].text)[0]["content"] == rule
    answer = json.loads(proposal.content[0].text)
    assert (answer["event"], answer["status"]) == ("PENDING", "candidate")
    [proposed] = json.loads(_cli("queue", "--ws", workspace).stdout)
    assert (str(proposed["memory_id"]), proposed["agent_id"]) == (answer["id"], "mcp")

    # 7. the ledger is current, and a user can be erased
    doctor = runner.invoke(cli.app, ["doctor"])
    assert "ok  version  schema version 2" in doctor.output.splitlines()
    assert anna.delete_all()["deleted"] >= 1
    assert [r for r in anna.search("answers in Portuguese") if r["scope"] == "user"] == []  # her private memory is gone
    assert bob.search("pump P4 suction valve")[0]["content"] == pump.content  # shared memory stays
