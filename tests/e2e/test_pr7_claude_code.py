"""PR 7 demo: `memhub ingest --source claude_code` on Claude Code sessions, a reviewer approves what should be shared,
then `memhub mcp` - started the way Claude Code starts it, in the project's folder - finds it with `search_memory`.
SQLite, fake models through `connect:`, a real stdio subprocess for the server; no Docker."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import yaml
from mcp import Client, StdioServerParameters
from typer.testing import CliRunner

from memhub import cli
from memhub.mcp_server import folder_workspace
from memhub.pipeline.extract import Candidate, Evidence
from tests.e2e import pr7_app
from tests.fakes import FakeChatModel, extraction

ROOT = Path(__file__).resolve().parents[2]
SESSION = "0b2f4c1e-7777-4a3d-8e21-000000000042"
runner = CliRunner()


def _event(kind: str, uuid: str, minute: int, content) -> dict:
    return {"type": kind, "sessionId": SESSION, "uuid": uuid, "timestamp": f"2026-11-01T10:{minute:02d}:00.000Z",
            "isSidechain": False, "message": {"role": kind, "content": content}}


def test_ingest_sessions_then_search_over_mcp(tmp_path, monkeypatch):
    project = (tmp_path / "my repo").resolve()
    project.mkdir()
    home = tmp_path / "home"
    workspace = folder_workspace(project)  # how Claude Code names this project's folder
    sessions = home / ".claude" / "projects" / workspace
    sessions.mkdir(parents=True)
    rule = "Integration tests need the pgvector container from docker compose"
    (sessions / f"{SESSION}.jsonl").write_text("\n".join(json.dumps(e) for e in [
        _event("user", "u1", 0, f"{rule}, remember that for this repo"),
        _event("assistant", "a1", 1, [{"type": "thinking", "thinking": "ok"}, {"type": "text", "text": "Noted."}]),
        _event("assistant", "a2", 2, [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]),
    ]) + "\n")
    env = {"HOME": str(home), "MEMHUB_USER": "anna", "PR7_DB": f"sqlite:///{project / 'memory.db'}"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(project)
    (project / "memhub.yaml").write_text(yaml.safe_dump({
        "project": "cc", "connect": "tests.e2e.pr7_app:build", "scopes": ["user", "workspace"],
        "types": {"fact": {"class": "memhub.types:Fact"}},
    }))
    assert runner.invoke(cli.app, ["init"]).exit_code == 0

    candidate = Candidate(type="fact", scope="workspace", fields={"content": rule}, utility=5, applies_generally=True,
                          assertion="stated", evidence=[Evidence(message_id="u1", quote=rule, claim_source="user")])
    monkeypatch.setattr(pr7_app, "LLM", FakeChatModel([extraction(candidate), extraction()], unrelated_by_default=True))
    ingest = runner.invoke(cli.app, ["ingest", "--source", "claude_code"])  # no `sources:` entry needed
    assert ingest.exit_code == 0, ingest.output
    assert json.loads(ingest.stdout)["created"] == 1

    queue = runner.invoke(cli.app, ["queue", "--ws", workspace])
    [pending] = json.loads(queue.stdout)
    assert (pending["content"], pending["status"]) == (rule, "candidate")
    assert runner.invoke(cli.app, ["approve", pending["id"]]).exit_code == 0

    server = StdioServerParameters(  # what `claude mcp add memhub -- memhub mcp` starts, in the project's folder
        command=sys.executable, args=["-m", "memhub.cli", "mcp"], cwd=str(project),
        env={**os.environ, **env, "PYTHONPATH": str(ROOT)},
    )

    async def ask():
        async with Client(server) as client:
            return await client.call_tool("search_memory", {"query": "integration tests pgvector docker"})

    result = anyio.run(ask)
    assert not result.is_error, result
    hits = json.loads(result.content[0].text)
    assert hits[0]["content"] == rule and hits[0]["scope"] == "workspace" and hits[0]["verified"] is True
