"""`memhub mcp`: the two tools over MCP, who a call runs as, and refusing to serve an unusable ledger. Tools are
called through the MCP SDK's in-process client, the way a real client calls them."""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import anyio
import pytest
from mcp import Client
from typer.testing import CliRunner

from memhub import Memory, cli
from memhub.mcp_server import build_server, folder_workspace, identity, new_run_id
from tests.fakes import FakeEmbeddings

ROOT = Path(__file__).resolve().parents[1]
MEM = "memory" + "://"
TYPES = {"fact": {"class": "memhub.types:Fact"}, "preference": {"class": "memhub.types:Preference", "keyed": True,
                                                                 "keys": {"language": "language"}}}
runner = CliRunner()


def _memory() -> Memory:
    m = Memory(db=MEM, embedder=FakeEmbeddings(16), config={"scopes": ["user", "workspace"], "types": TYPES})
    m.init()
    return m


def _call(server, *calls: tuple[str, dict]) -> list:
    async def run():
        async with Client(server) as client:
            return [await client.call_tool(name, args) for name, args in calls]

    return anyio.run(run)


def _json(result) -> object:
    assert not result.is_error, result
    return json.loads(result.content[0].text)


def test_tools_and_their_schemas():
    server = build_server(_memory(), workspace_id="acme", user_id="anna", run_id="mcp-x")

    async def run():
        async with Client(server) as client:
            return {t.name: t.input_schema for t in (await client.list_tools()).tools}

    tools = anyio.run(run)
    assert set(tools) == {"search_memory", "propose_memory"}
    search, propose = tools["search_memory"], tools["propose_memory"]
    assert search["required"] == ["query"] and set(search["properties"]) == {"query", "type"}
    assert json.dumps(search["properties"]["type"]).count('"fact"') == 1 and '"preference"' in json.dumps(search["properties"]["type"])
    assert sorted(propose["required"]) == ["fields", "type"]
    assert set(propose["properties"]) == {"type", "fields", "evidence", "scope"}
    assert propose["properties"]["type"]["enum"] == ["fact", "preference"]
    assert propose["properties"]["scope"]["enum"] == ["user", "workspace"]


def test_search_memory():
    m = _memory()
    anna, bob, lead = (dict(workspace_id="acme", user_id=u, run_id="r") for u in ("anna", "bob", "lead"))
    m.add("Anna deploys with pnpm", **anna)
    m.add("Bob deploys with yarn", **bob)
    m.add({"content": "The team deploys on Fridays"}, type="fact", scope="workspace", roles=["workspace_admin"], **lead)
    m.add({"content": "The team deploys with docker"}, type="fact", scope="workspace", **anna)  # a candidate
    server = build_server(m, workspace_id="acme", user_id="anna", run_id="mcp-x")
    [result] = _call(server, ("search_memory", {"query": "team deploys with pnpm yarn docker fridays"}))
    hits = _json(result)
    assert sorted(h["content"] for h in hits) == ["Anna deploys with pnpm", "The team deploys on Fridays"]
    assert all(set(h) == {"id", "type", "content", "verified", "scope", "as_of", "inferred"} for h in hits)


def test_propose_memory():
    m = _memory()
    run_id = new_run_id()
    server = build_server(m, workspace_id="acme", user_id="anna", run_id=run_id)
    [result] = _call(server, ("propose_memory", {"type": "fact", "fields": {"content": "CI runs on every push"}, "scope": "workspace"}))
    answer = _json(result)
    [row] = m.queue(workspace_id="acme", user_id="lead", run_id="r", roles=["workspace_admin"])
    assert answer == {"id": str(row["memory_id"]), "event": "PENDING", "status": "candidate"}
    assert run_id.startswith("mcp-") and (row["run_id"], row["agent_id"], row["status"]) == (run_id, "mcp", "candidate")


def test_invalid_calls_do_not_stop_the_server():
    server = build_server(_memory(), workspace_id="acme", user_id="anna", run_id="mcp-x")
    unknown, invalid, missing, fine = _call(
        server,
        ("propose_memory", {"type": "fact", "fields": {}}),
        ("propose_memory", {"type": "preference", "fields": {"content": "x", "key": "not-a-key"}}),
        ("search_memory", {}),
        ("search_memory", {"query": "anything"}),
    )
    assert "rejected" in _json(unknown) and "rejected" in _json(invalid)
    assert missing.is_error
    assert _json(fine) == []
    [unknown_type] = _call(server, ("propose_memory", {"type": "no-such-type", "fields": {"content": "x"}}))
    assert unknown_type.is_error or "rejected" in json.loads(unknown_type.content[0].text)


def test_identity(tmp_path, monkeypatch):
    assert identity("acme", "anna") == ("acme", "anna")
    assert identity(None, None, cwd=Path("/a/b c.d"))[0] == "-a-b-c-d"
    assert folder_workspace(Path("/Users/me/My Drive/x_y")) == "-Users-me-My-Drive-x-y"
    import getpass

    assert identity(None, None, cwd=Path("/w"))[1] == getpass.getuser()
    assert new_run_id() != new_run_id()

    seen = {}
    monkeypatch.setattr(cli, "_resolve", lambda config: None)
    monkeypatch.setattr(cli, "_require_db_and_embedder", lambda resolved: None)
    monkeypatch.setattr(cli, "_parts", lambda resolved: type("Parts", (), {"db": None, "embedder": None})())

    class Stop(Exception):
        pass

    def fake_build(memory, *, workspace_id, user_id, run_id):
        seen.update(ws=workspace_id, user=user_id)
        raise Stop

    import memhub.mcp_server as mcp_server

    monkeypatch.setattr(mcp_server, "build_server", fake_build)
    monkeypatch.setattr("memhub.memory.Memory.__init__", lambda self, **kw: None)
    monkeypatch.setattr(cli, "read_context", lambda: {"workspace": "from-use", "user": "use-user"})
    monkeypatch.setenv("MEMHUB_WORKSPACE", "from-env")
    runner.invoke(cli.app, ["mcp", "--ws", "from-flag"])
    assert seen["ws"] == "from-flag"
    runner.invoke(cli.app, ["mcp"])
    assert seen == {"ws": "from-env", "user": "use-user"}
    monkeypatch.delenv("MEMHUB_WORKSPACE")
    runner.invoke(cli.app, ["mcp"])
    assert seen["ws"] == "from-use"
    monkeypatch.setattr(cli, "read_context", lambda: {"workspace": None, "user": None})
    monkeypatch.chdir(tmp_path)
    runner.invoke(cli.app, ["mcp"])
    assert seen == {"ws": folder_workspace(Path.cwd()), "user": getpass.getuser()}


def test_mcp_fails_before_serving(tmp_path, monkeypatch):
    import yaml

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(16))
    base = {"project_prefix": "mcp", "database_url": f"sqlite:///{tmp_path / 'm.db'}", "scopes": ["user"],
            "types": {"fact": {"class": "memhub.types:Fact"}}}
    (tmp_path / "memhub.yaml").write_text(yaml.safe_dump(base))  # no embedder
    no_embedder = runner.invoke(cli.app, ["mcp"])
    assert no_embedder.exit_code == 1 and "embeddings" in no_embedder.output

    base["embeddings"] = {"provider": "openai", "model": "fake-embed", "api_key_env": "UNSET", "dims": 16}
    (tmp_path / "memhub.yaml").write_text(yaml.safe_dump(base))
    no_tables = runner.invoke(cli.app, ["mcp"])
    assert no_tables.exit_code == 1 and "init" in no_tables.output

    monkeypatch.setitem(sys.modules, "mcp.server.mcpserver", None)
    no_sdk = runner.invoke(cli.app, ["mcp"])
    assert no_sdk.exit_code == 1 and "pip install memhub[mcp]" in no_sdk.output


def test_mcp_is_an_extra():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert not any(d.startswith("mcp") for d in project["dependencies"])
    assert any(d.startswith("mcp") for d in project["optional-dependencies"]["mcp"])
    assert any(d.startswith("mcp") for d in project["optional-dependencies"]["dev"])


def test_mcp_docs():
    text = (ROOT / "docs" / "claude-code.md").read_text()
    for needle in ("claude mcp add memhub -- memhub mcp", "memhub ingest --source claude_code", "memhub queue",
                   "MEMHUB_WORKSPACE", "pip install memhub[mcp]"):
        assert needle in text, needle
    assert "docs/claude-code.md" in (ROOT / "README.md").read_text()
