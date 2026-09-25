"""CLI tests: the real typer app, against a real pgvector Postgres, fake embeddings."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from typer.testing import CliRunner

from memhub import cli
from memhub.store import MemoryStore
from tests.conftest import TEST_DIMS, TEST_EMBEDDING_MODEL
from tests.fakes import FakeEmbeddings

runner = CliRunner()


def _config_dict(dsn: str, prefix: str, *, embed_model: str = TEST_EMBEDDING_MODEL) -> dict:
    return {
        "project_prefix": prefix,
        "database_url": dsn,
        "workspace_default": "ws",
        "llm": {
            "extractor": {"provider": "openai", "model": "x", "api_key_env": "UNSET"},
            "judge": {"provider": "openai", "model": "x", "api_key_env": "UNSET"},
        },
        "embeddings": {"provider": "openai", "model": embed_model, "api_key_env": "UNSET", "dims": TEST_DIMS},
        "scopes": ["user", "workspace"],
        "types": {
            "fact": {"class": "memhub.types:Fact", "type_prior": 0.6},
            "preference": {
                "class": "memhub.types:Preference", "type_prior": 0.8, "keyed": True,
                "keys": {"language": "answer language", "answer_style": "answer style", "theme": "theme", "style": "style"},
            },
            "profile": {
                "class": "memhub.types:Profile", "type_prior": 0.8, "keyed": True,
                "keys": {"nationality": "nationality", "city": "city"},
            },
            "episode": {"class": "memhub.types:Episode", "type_prior": 0.4},
        },
        "admission": {"weights": {"utility": 1.0}, "threshold": 0.5},
    }


@pytest.fixture
def cli_env(pg_dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(dims=cfg.dims))
    prefix = f"c{uuid.uuid4().hex[:8]}"
    config_path = tmp_path / "memhub.yaml"
    config_path.write_text(yaml.safe_dump(_config_dict(pg_dsn, prefix)))

    def invoke(*args: str, roles: str = "workspace_admin", config=config_path):
        return runner.invoke(cli.app, [*args, "--config", str(config)], env={"MEMHUB_CLI_ROLES": roles})

    def write_json(name: str, data: dict):
        path = tmp_path / name
        path.write_text(json.dumps(data))
        return str(path)

    result = invoke("init")
    assert result.exit_code == 0, result.output
    invoke.write_json = write_json
    invoke.store = MemoryStore(pg_dsn, prefix)
    invoke.config_path = config_path
    invoke.dsn = pg_dsn
    invoke.prefix = prefix
    return invoke


def _out(result):
    return json.loads(result.stdout)


def test_init_is_idempotent(cli_env):
    assert cli_env("init").exit_code == 0


def test_add_list_search_roundtrip(cli_env):
    f = cli_env.write_json("m.json", {"content": "alice likes tea"})
    added = cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice", "--file", f)
    assert added.exit_code == 0, added.output
    assert _out(added)["status"] == "active"

    listed = _out(cli_env("list", "--user", "alice"))
    assert [r["content"] for r in listed] == ["alice likes tea"]

    found = _out(cli_env("search", "alice likes tea", "--user", "alice"))
    assert found[0]["content"] == "alice likes tea"


def test_add_invalid_fields_writes_nothing(cli_env):
    f = cli_env.write_json("bad.json", {"content": "likes dark mode"})  # preference needs `key`
    result = cli_env("add", "--type", "preference", "--scope", "user", "--user", "alice", "--file", f)
    assert result.exit_code == 1
    assert "ValidationError" in result.output
    assert _out(cli_env("list")) == []


def test_edit_archive_delete_flow(cli_env):
    f = cli_env.write_json("m.json", {"content": "old"})
    row = _out(cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice", "--file", f))
    edit_file = cli_env.write_json("e.json", {"content": "new"})
    edited = _out(cli_env("edit", row["memory_id"], "--file", edit_file))
    assert edited["version"] == 2 and edited["content"] == "new"

    archived = _out(cli_env("archive", edited["memory_id"]))
    assert archived["status"] == "archived"

    # archived row can't be deleted by a non-owner-non-rejected combination... an admin isn't the owner:
    denied = cli_env("delete", archived["id"])
    assert denied.exit_code == 1
    assert "PermissionDenied" in denied.output


def test_delete_user(cli_env):
    f = cli_env.write_json("m.json", {"content": "x"})
    cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice", "--file", f)
    result = cli_env("delete", "--user", "alice")
    assert result.exit_code == 0
    assert _out(result)["memory"] == 1
    assert _out(cli_env("list", "--user", "alice")) == []


def test_delete_needs_id_or_user(cli_env):
    assert cli_env("delete").exit_code == 1


def test_queue_approve_and_reject(cli_env):
    f = cli_env.write_json("m.json", {"content": "shared fact one"})
    # a non-admin proposing a workspace memory becomes a candidate
    cand = _out(cli_env("add", "--type", "fact", "--scope", "workspace", "--file", f, roles="reader"))
    assert cand["status"] == "candidate"
    queued = _out(cli_env("queue"))
    assert [r["id"] for r in queued] == [cand["id"]]

    approved = _out(cli_env("approve", cand["id"], "--note", "ok"))
    assert approved["status"] == "active" and approved["verified"] is True

    f2 = cli_env.write_json("m2.json", {"content": "shared fact two"})
    cand2 = _out(cli_env("add", "--type", "fact", "--scope", "workspace", "--file", f2, roles="reader"))
    rejected = _out(cli_env("reject", cand2["id"]))
    assert rejected["status"] == "rejected"


def test_non_admin_cannot_approve(cli_env):
    f = cli_env.write_json("m.json", {"content": "shared"})
    cand = _out(cli_env("add", "--type", "fact", "--scope", "workspace", "--file", f, roles="reader"))
    result = cli_env("approve", cand["id"], roles="reader")
    assert result.exit_code == 1
    assert "PermissionDenied" in result.output


def test_promote_creates_workspace_candidate(cli_env):
    f = cli_env.write_json("m.json", {"content": "user fact"})
    row = _out(cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice", "--file", f))
    promoted = _out(cli_env("promote", row["memory_id"]))
    assert promoted["scope"] == "workspace" and promoted["status"] == "candidate"


def test_embedding_mismatch_makes_commands_refuse_until_reembed(cli_env, pg_dsn):
    f = cli_env.write_json("m.json", {"content": "alice likes tea"})
    cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice", "--file", f)

    changed = cli_env.config_path.parent / "changed.yaml"
    changed.write_text(yaml.safe_dump(_config_dict(pg_dsn, cli_env.prefix, embed_model="another-embed-model")))

    refused = cli_env("list", config=changed)
    assert refused.exit_code == 1
    assert "reembed" in refused.output

    ok = cli_env("reembed", config=changed)
    assert ok.exit_code == 0, ok.output
    assert "reembedded 1" in ok.output
    assert cli_env("list", config=changed).exit_code == 0


def test_reembed_requires_admin(cli_env):
    result = cli_env("reembed", roles="reader")
    assert result.exit_code == 1
    assert "PermissionDenied" in result.output


def test_runs_empty(cli_env):
    assert _out(cli_env("runs")) == []


def test_add_stores_now_without_a_date_and_the_files_date_with_one(cli_env):
    plain = _out(cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice",
                         "--file", cli_env.write_json("a.json", {"content": "alice likes tea"})))
    assert abs(datetime.fromisoformat(plain["observed_at"]) - datetime.now(timezone.utc)) < timedelta(minutes=1)
    dated = _out(cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice", "--file",
                         cli_env.write_json("b.json", {"content": "alice likes jam", "observed_at": "2025-03-01T10:00:00+00:00"})))
    assert dated["observed_at"].startswith("2025-03-01 10:00:00")
    assert dated["evidence"][0]["observed_at"].startswith("2025-03-01T10:00:00")
    assert "observed_at" not in dated["payload"]


def test_list_and_search_show_observed_at(cli_env):
    f = cli_env.write_json("m.json", {"content": "alice likes tea", "observed_at": "2025-03-01T10:00:00+00:00"})
    cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice", "--file", f)
    assert _out(cli_env("list"))[0]["observed_at"].startswith("2025-03-01")
    assert _out(cli_env("search", "alice likes tea", "--user", "alice"))[0]["observed_at"].startswith("2025-03-01")


def test_list_and_search_show_assertion(cli_env):
    cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice", "--file",
            cli_env.write_json("m.json", {"content": "alice likes tea"}))
    assert _out(cli_env("list"))[0]["assertion"] == "stated"
    assert _out(cli_env("search", "alice likes tea", "--user", "alice"))[0]["assertion"] == "stated"


def test_list_stale_and_search_include_stale(cli_env):
    cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice", "--file",
            cli_env.write_json("a.json", {"content": "alice likes tea"}))
    cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice", "--file",
            cli_env.write_json("b.json", {"content": "alice likes jam"}))
    with cli_env.store.connect() as conn, conn.cursor() as cur:  # expire one of them
        cur.execute(f"UPDATE {cli_env.store._t('memory')} SET valid_until = now() - interval '1 day' WHERE content = 'alice likes tea'")
    assert {r["content"]: r["stale"] for r in _out(cli_env("list"))} == {"alice likes tea": True, "alice likes jam": False}
    assert [r["content"] for r in _out(cli_env("list", "--stale"))] == ["alice likes tea"]
    assert [r["content"] for r in _out(cli_env("search", "alice likes", "--user", "alice"))] == ["alice likes jam"]
    assert len(_out(cli_env("search", "alice likes", "--user", "alice", "--include-stale"))) == 2


def test_add_profile_with_a_key_outside_the_config_fails_and_writes_nothing(cli_env):
    bad = cli_env.write_json("p.json", {"key": "hobby", "content": "likes tea"})
    result = cli_env("add", "--type", "profile", "--scope", "user", "--user", "alice", "--file", bad)
    assert result.exit_code == 1 and "unknown key" in result.output
    assert _out(cli_env("list")) == []
    good = cli_env.write_json("q.json", {"key": "city", "content": "Lives in Lyon"})
    assert cli_env("add", "--type", "profile", "--scope", "user", "--user", "alice", "--file", good).exit_code == 0
    assert [r["key"] for r in _out(cli_env("list", "--user", "alice"))] == ["city"]
    again = cli_env("add", "--type", "profile", "--scope", "user", "--user", "alice", "--file", good)
    assert again.exit_code == 1 and "exists" in again.output


def test_search_shows_the_terms_that_expanded_the_query(cli_env):
    cfg = yaml.safe_load(cli_env.config_path.read_text())
    cfg["types"]["term"] = {"class": "memhub.types:Term", "scopes": ["workspace"]}
    cli_env.config_path.write_text(yaml.safe_dump(cfg))
    term = cli_env.write_json("t.json", {
        "content": "CNH: carteira de motorista", "term": "CNH", "aliases": ["carteira de motorista"]})
    assert cli_env("add", "--type", "term", "--scope", "workspace", "--file", term).exit_code == 0
    fact = cli_env.write_json("f.json", {"content": "quer trocar a carteira de motorista"})
    cli_env("add", "--type", "fact", "--scope", "user", "--user", "alice", "--file", fact)
    result = cli_env("search", "trocar a CNH", "--user", "alice", "--type", "fact")
    assert result.exit_code == 0, result.output
    assert "query expanded by CNH" in result.stderr
    assert _out(result)[0]["expanded_by"] == ["CNH"]
    plain = cli_env("search", "trocar a licence", "--user", "alice", "--type", "fact")
    assert "expanded" not in plain.stderr


def test_history_lists_every_version_with_value_time_evidence_and_creator(cli_env):
    f = cli_env.write_json("m.json", {"content": "lives in Grenoble", "key": "city"})
    row = _out(cli_env("add", "--type", "profile", "--scope", "user", "--user", "alice", "--file", f))
    edit_file = cli_env.write_json("e.json", {"content": "lives in Paris"})
    cli_env("edit", row["memory_id"], "--file", edit_file)
    versions = _out(cli_env("history", row["memory_id"]))
    assert [(v["version"], v["status"], v["value"], v["key"]) for v in versions] == [
        (1, "superseded", "lives in Grenoble", "city"), (2, "active", "lives in Paris", "city")]
    assert all(v["observed_at"] and v["evidence"] and v["created_by"] for v in versions)
    assert cli_env("history", str(uuid.uuid4())).exit_code == 1
