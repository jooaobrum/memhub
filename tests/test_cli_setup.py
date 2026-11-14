"""`memhub init`, `config show|set`, `doctor`, `use`, the context and `-c` optional: the real typer app, a real pgvector."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
import yaml
from psycopg_pool import ConnectionPool
from typer.testing import CliRunner

from memhub import Memory, cli, cli_setup
from memhub.settings import SEARCH_PLACES, context_path, resolve
from tests import connect_apps
from tests.fakes import FakeChatModel, FakeEmbeddings

runner = CliRunner()
CONNECT = "tests.connect_apps:build"


def _prefix() -> str:
    return f"s{uuid.uuid4().hex[:8]}"


def _cwd(tmp_path: Path, monkeypatch, name: str = "app") -> Path:
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    monkeypatch.chdir(folder)
    return folder


def _yaml_file(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


def _inits(monkeypatch, dims: int = 16, events: list | None = None):
    """`init_embeddings` (what `models.embeddings` goes through) answers with fakes; `events` records the order."""
    import langchain.embeddings as emb_mod

    class Emb(FakeEmbeddings):
        def embed_query(self, text):
            if events is not None:
                events.append("embed")
            return super().embed_query(text)

    monkeypatch.setattr(emb_mod, "init_embeddings", lambda spec, **kw: Emb(dims))


@pytest.fixture(autouse=True)
def _fresh_state():
    connect_apps.reset()
    yield
    connect_apps.reset()


def _tables(dsn: str, prefix: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        return [t for t in (f"{prefix}_memory", f"{prefix}_memory_runs") if conn.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0]]


def _loaded(path: Path | None = None) -> dict:
    return yaml.safe_load((path or Path.cwd() / "memhub.yaml").read_text())


def _lines(output: str) -> list[tuple[str, str, str]]:
    return [tuple(line.split("  ", 2)) for line in output.splitlines() if line.strip()]  # type: ignore[misc]


# --- init -------------------------------------------------------------------------------------------------------


def test_init_with_flags_writes_yaml_and_tables(pg_dsn, monkeypatch):
    _inits(monkeypatch)
    p = _prefix()
    result = runner.invoke(cli.app, ["init", "--project", p, "--db", pg_dsn, "--embeddings", "fake:emb"])
    assert result.exit_code == 0, result.output
    assert f"initialized {p}_memory / {p}_memory_runs" in result.output
    data = _loaded()
    assert data == {"project": p, "db": pg_dsn, "models": {"embeddings": "fake:emb"}, "memory": "./memory.md", "scopes": ["user"]}
    assert resolve(Path.cwd() / "memhub.yaml").config.project_prefix == p
    assert _tables(pg_dsn, p) == [f"{p}_memory", f"{p}_memory_runs"]


def test_init_accepts_literal_url_password(pg_dsn, monkeypatch):
    _inits(monkeypatch)
    assert "postgres:postgres@" in pg_dsn  # a URL with a literal user and password
    result = runner.invoke(cli.app, ["init", "--project", _prefix(), "--db", pg_dsn, "--embeddings", "fake:emb"])
    assert result.exit_code == 0, result.output
    assert _loaded()["db"] == pg_dsn
    # written as given (AD-007), with a warning on stderr that never repeats the password
    assert "literal password" in result.stderr and "${MEMHUB_DB_PASSWORD}" in result.stderr
    assert ":postgres@" not in result.stderr and "literal password" not in result.stdout


def test_init_does_not_warn_without_a_literal_password(pg_dsn, tmp_path, monkeypatch):
    _inits(monkeypatch)
    monkeypatch.setenv("PG_PW", "postgres")
    with_ref = pg_dsn.replace(":postgres@", ":${PG_PW}@")
    result = runner.invoke(cli.app, ["init", "--project", _prefix(), "--db", with_ref, "--embeddings", "fake:emb"])
    assert result.exit_code == 0, result.output
    assert _loaded()["db"] == with_ref and "literal password" not in result.output
    Path("memhub.yaml").unlink()
    lite = runner.invoke(cli.app, ["init", "--project", _prefix(), "--db", f"sqlite:///{tmp_path / 'm.db'}",
                                   "--embeddings", "fake:emb"])
    assert lite.exit_code == 0, lite.output
    assert "literal password" not in lite.output


def test_init_connect_excludes_db_and_models(pg_dsn, tmp_path, monkeypatch):
    connect_apps.reset(db=pg_dsn, embedder=FakeEmbeddings(16))
    p = _prefix()
    result = runner.invoke(cli.app, ["init", "--project", p, "--connect", CONNECT])
    assert result.exit_code == 0, result.output
    data = _loaded()
    assert data["connect"] == CONNECT and "db" not in data and "models" not in data
    other = _cwd(tmp_path, monkeypatch, "other")
    result = runner.invoke(cli.app, ["init", "--project", p, "--connect", CONNECT, "--db", pg_dsn])
    assert result.exit_code == 1 and "--connect" in result.output and "--db" in result.output
    assert not (other / "memhub.yaml").exists()


def test_init_chat_flag(pg_dsn, tmp_path, monkeypatch):
    _inits(monkeypatch)
    base = ["init", "--db", pg_dsn, "--embeddings", "fake:emb"]
    assert runner.invoke(cli.app, [*base, "--project", _prefix(), "--chat", "fake:chat"]).exit_code == 0
    assert _loaded()["models"] == {"chat": "fake:chat", "embeddings": "fake:emb"}
    _cwd(tmp_path, monkeypatch, "nochat")
    assert runner.invoke(cli.app, [*base, "--project", _prefix()]).exit_code == 0
    assert "chat" not in _loaded()["models"]


def test_init_prompts_for_missing_values(pg_dsn, tmp_path, monkeypatch):
    _inits(monkeypatch)
    _cwd(tmp_path, monkeypatch, "My App-2")
    # project (default), connect (empty: a db URL instead), db, embeddings, chat (empty), what to keep (empty)
    result = runner.invoke(cli.app, ["init"], input=f"\n\n{pg_dsn}\nfake:emb\n\n\n")
    assert result.exit_code == 0, result.output
    assert _loaded() == {"project": "my_app_2", "db": pg_dsn, "models": {"embeddings": "fake:emb"}, "memory": "./memory.md", "scopes": ["user"]}
    # flags mean a script or a coding agent: nothing is asked, what was not given takes its default
    # (the folder's project, the starter memory.md, no chat model)
    _cwd(tmp_path, monkeypatch, "second")
    result = runner.invoke(cli.app, ["init", "--db", pg_dsn, "--embeddings", "fake:emb"], input="")
    assert result.exit_code == 0, result.output
    assert "chat" not in _loaded()["models"] and _loaded()["memory"] == "./memory.md" and _loaded()["project"] == "second"
    # flags without a database or an embedding model stop with the flags to add, instead of asking
    _cwd(tmp_path, monkeypatch, "fourth")
    result = runner.invoke(cli.app, ["init", "--chat", "fake:chat"], input="")
    assert result.exit_code == 1 and "--db and --embeddings" in result.output
    # with --connect the models are not asked either: no input is needed at all
    _cwd(tmp_path, monkeypatch, "third")
    connect_apps.reset(db=pg_dsn, embedder=FakeEmbeddings(16))
    result = runner.invoke(cli.app, ["init", "--project", _prefix(), "--connect", CONNECT], input="")
    assert result.exit_code == 0, result.output


def test_init_order_probe_before_tables_before_yaml(pg_dsn, monkeypatch):
    events: list[str] = []
    _inits(monkeypatch, events=events)
    from memhub.store import MemoryStore

    real_init, real_write = MemoryStore.init, cli_setup.write_yaml
    monkeypatch.setattr(MemoryStore, "init", lambda self, **kw: events.append("tables") or real_init(self, **kw))
    monkeypatch.setattr(cli_setup, "write_yaml", lambda path, data: events.append("write") or real_write(path, data))
    result = runner.invoke(cli.app, ["init", "--project", _prefix(), "--db", pg_dsn, "--embeddings", "fake:emb"])
    assert result.exit_code == 0, result.output
    assert events == ["embed", "tables", "write"]


def test_init_failure_writes_no_yaml(pg_dsn, tmp_path, monkeypatch):
    import langchain.embeddings as emb_mod

    base = ["init", "--embeddings", "fake:emb"]
    # 1. the probe embed fails
    class Broken:
        def embed_query(self, text):
            raise RuntimeError("no network")

    monkeypatch.setattr(emb_mod, "init_embeddings", lambda spec, **kw: Broken())
    result = runner.invoke(cli.app, [*base, "--project", _prefix(), "--db", pg_dsn])
    assert result.exit_code == 1 and "no network" in result.output and not Path("memhub.yaml").exists()
    # 2. the database is unreachable
    _inits(monkeypatch)
    result = runner.invoke(cli.app, [*base, "--project", _prefix(), "--db", "postgresql://nobody@127.0.0.1:1/none"])
    assert result.exit_code == 1 and not Path("memhub.yaml").exists()
    # 3. the ledger was built with other dims
    p = _prefix()
    _cwd(tmp_path, monkeypatch, "first")
    assert runner.invoke(cli.app, [*base, "--project", p, "--db", pg_dsn]).exit_code == 0
    second = _cwd(tmp_path, monkeypatch, "second")
    _inits(monkeypatch, dims=8)
    result = runner.invoke(cli.app, [*base, "--project", p, "--db", pg_dsn])
    assert result.exit_code == 1 and "reembed" in result.output and not (second / "memhub.yaml").exists()


def test_init_rejects_bad_project(monkeypatch):
    events: list[str] = []
    _inits(monkeypatch, events=events)
    unreachable = "postgresql://nobody@127.0.0.1:1/none"
    result = runner.invoke(cli.app, ["init", "--project", "1bad-name", "--db", unreachable, "--embeddings", "fake:emb"])
    assert result.exit_code == 1 and "letters, digits and underscores" in result.output
    assert events == [] and not Path("memhub.yaml").exists()  # nothing was built or dialled


def test_init_existing_yaml_needs_force(pg_dsn, monkeypatch):
    _inits(monkeypatch)
    p = _prefix()
    args = ["init", "--project", p, "--db", pg_dsn, "--embeddings", "fake:emb"]
    assert runner.invoke(cli.app, args).exit_code == 0
    before = Path("memhub.yaml").read_bytes()
    refused = runner.invoke(cli.app, [*args, "--chat", "fake:chat"])
    assert refused.exit_code == 1 and "--force" in refused.output and Path("memhub.yaml").read_bytes() == before
    with psycopg.connect(pg_dsn) as conn:
        oid = conn.execute("SELECT %s::regclass::oid", (f"{p}_memory",)).fetchone()[0]
        forced = runner.invoke(cli.app, [*args, "--chat", "fake:chat", "--force"])
        assert forced.exit_code == 0, forced.output
        assert conn.execute("SELECT %s::regclass::oid", (f"{p}_memory",)).fetchone()[0] == oid  # tables untouched
    assert _loaded()["models"]["chat"] == "fake:chat"


# --- init writes the memory file (C20, C24-C27) -----------------------------------------------------------------


def _lite(tmp_path: Path) -> list[str]:
    return ["--db", f"sqlite:///{tmp_path / f'{_prefix()}.db'}", "--embeddings", "fake:emb"]


def test_init_preset_flag_is_gone(tmp_path, monkeypatch):
    """C20: `--preset` is no option any more: typer's usage error, exit 2, nothing written."""
    _inits(monkeypatch)
    result = runner.invoke(cli.app, ["init", *_lite(tmp_path), "--preset", "support"])
    assert result.exit_code == 2 and not Path("memhub.yaml").exists() and not Path("memory.md").exists()


def test_init_flags_writes_memory_file(tmp_path, monkeypatch):
    """C24: flags, no --memory: nothing asked, memhub.yaml names ./memory.md, the starter file resolves."""
    _inits(monkeypatch)
    result = runner.invoke(cli.app, ["init", *_lite(tmp_path)], input="")
    assert result.exit_code == 0, result.output
    assert cli_setup.KEEP_QUESTION not in result.output
    assert _loaded()["memory"] == "./memory.md" and _loaded()["scopes"] == ["user"]  # AD-035
    text = Path("memory.md").read_text()
    front = yaml.safe_load(text.split("---")[1])
    assert front == {"review": "workspace", "types": {"fact": "builtin", "preference": "builtin", "profile": "builtin"}}  # C31 (#29)
    body = text.split("---", 2)[2].strip()
    assert body.startswith(f"# What to remember\n\n{cli_setup.DEFAULT_BODY}\n") and body.endswith(cli_setup.STARTER_RULES.strip())
    cfg = resolve(Path.cwd() / "memhub.yaml").config
    assert {"fact", "preference", "profile"} <= set(cfg.types) and cfg.extraction.instructions.startswith("# What to remember")


def test_init_asks_what_to_keep(tmp_path, monkeypatch):
    """C25: interactive init asks one question; the answer is the body; an empty answer, the default body."""
    _inits(monkeypatch)
    _cwd(tmp_path, monkeypatch, "asked")
    db = f"sqlite:///{tmp_path / 'a.db'}"
    # project, connect (empty), db, embeddings, chat (empty), what to keep
    result = runner.invoke(cli.app, ["init"], input=f"\n\n{db}\nfake:emb\n\nOnly the club's bird sightings.\n")
    assert result.exit_code == 0, result.output
    assert "In a sentence, what should this memory keep?" in result.output
    body = Path("memory.md").read_text().split("---", 2)[2].strip()
    assert body.startswith("# What to remember\n\nOnly the club's bird sightings.\n") and cli_setup.DEFAULT_BODY not in body
    assert body.endswith(cli_setup.STARTER_RULES.strip())  # the general rules follow the project's own line (AD-034)
    _cwd(tmp_path, monkeypatch, "empty")
    result = runner.invoke(cli.app, ["init"], input=f"\n\n{db}\nfake:emb\n\n\n")
    assert result.exit_code == 0, result.output
    assert cli_setup.DEFAULT_BODY in Path("memory.md").read_text()


def test_init_with_memory_path(tmp_path, monkeypatch):
    """C26: --memory names the user's file and writes no memory.md; a missing file exits 1."""
    _inits(monkeypatch)
    Path("mine.md").write_text("---\ntypes:\n  note: \"{text}\"\n---\nKeep notes.\n")
    result = runner.invoke(cli.app, ["init", *_lite(tmp_path), "--memory", "./mine.md"])
    assert result.exit_code == 0, result.output
    assert _loaded()["memory"] == "./mine.md" and "scopes" not in _loaded() and not Path("memory.md").exists()
    assert set(resolve(Path.cwd() / "memhub.yaml").config.types) == {"note"}
    _cwd(tmp_path, monkeypatch, "missing")
    result = runner.invoke(cli.app, ["init", *_lite(tmp_path), "--memory", "./nope.md"])
    assert result.exit_code == 1 and "memory file not found: ./nope.md" in result.output
    assert not Path("memhub.yaml").exists()


def test_init_keeps_memory_file(tmp_path, monkeypatch):
    """C27: an existing memory.md is kept byte for byte unless --force; `kept memory.md` on stderr."""
    _inits(monkeypatch)
    mine = "---\ntypes:\n  fact: builtin\n---\nMy own words.\n"
    Path("memory.md").write_text(mine)
    args = ["init", *_lite(tmp_path)]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert Path("memory.md").read_text() == mine and "kept memory.md" in result.stderr
    result = runner.invoke(cli.app, [*args, "--force"])
    assert result.exit_code == 0, result.output
    assert Path("memory.md").read_text() != mine and cli_setup.DEFAULT_BODY in Path("memory.md").read_text()


def test_config_show_names_the_memory_file():
    """C7: every key the memory file sets shows source memory.md; no row shows preset."""
    _yaml_file(Path("memhub.yaml"), {"project": "birds", "memory": "./memory.md"})
    Path("memory.md").write_text("---\nstrict: true\ntypes:\n  sighting: \"Saw {species}\"\nareas:\n  owls: owls\n---\nOnly birds.\n")
    result = runner.invoke(cli.app, ["config", "show"], env={})
    assert result.exit_code == 0, result.output
    shown = {k: (v, src) for k, v, src in _lines(result.output)}
    for key in ("strict", "areas.open", "areas.seeds", "extraction.instructions", "types.sighting.fields.species", "types.sighting.area"):
        assert shown[key][1] == "memory.md", key
    assert shown["memory"] == ("./memory.md", "yaml")
    assert not any("preset" in k or src == "preset" for k, (_, src) in shown.items())


def _legacy_yaml(dsn: str, prefix: str) -> dict:
    return {
        "project_prefix": prefix, "database_url": dsn, "workspace_default": "ws",
        "llm": {"extractor": {"provider": "openai", "model": "x"}, "judge": {"provider": "openai", "model": "x"}},
        "embeddings": {"provider": "openai", "model": "fake-embed", "api_key_env": "UNSET", "dims": 16},
        "scopes": ["user", "workspace"],
        "types": {"fact": {"class": "memhub.types:Fact", "type_prior": 0.6}},
    }


def test_init_with_c_or_no_flags_keeps_old_behavior(pg_dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(cfg.dims))
    p = _prefix()
    named = _yaml_file(tmp_path / "named.yaml", _legacy_yaml(pg_dsn, p))
    before = named.read_bytes()
    result = runner.invoke(cli.app, ["init", "-c", str(named)])
    assert result.exit_code == 0, result.output
    assert f"initialized {p}_memory" in result.output and named.read_bytes() == before
    assert not Path("memhub.yaml").exists()
    p2 = _prefix()
    here = _yaml_file(Path("memhub.yaml"), _legacy_yaml(pg_dsn, p2))
    before = here.read_bytes()
    result = runner.invoke(cli.app, ["init"])  # no flags, a yaml found
    assert result.exit_code == 0, result.output
    assert here.read_bytes() == before and _tables(pg_dsn, p2) == [f"{p2}_memory", f"{p2}_memory_runs"]
    here.unlink()
    result = runner.invoke(cli.app, ["init"], input="")  # no flags, no yaml: it asks
    assert result.exit_code != 0 and "Project" in result.output


# --- config show / set ------------------------------------------------------------------------------------------


def _show(*args: str, env: dict | None = None):
    result = runner.invoke(cli.app, ["config", "show", *args], env=env or {})
    assert result.exit_code == 0, result.output
    return {k: (v, s) for k, v, s in _lines(result.output)}


def test_config_show_lines_and_sources():
    _yaml_file(Path("memhub.yaml"), {"project": "minds"})
    shown = _show(env={"MEMHUB_INGESTION__MAX_CANDIDATES": "5"})
    assert shown["project"] == ("minds", "yaml")
    assert shown["ingestion.max_candidates"] == ("5", "env")
    assert "terms.max_aliases" not in shown
    assert _show("--all")["terms.max_aliases"] == ("5", "default")


def test_config_show_masks_secrets():
    secrets = {"api_key": "sk-literal-1", "token": "tok-literal-2", "password": "pw-literal-3", "secret": "sec-literal-4"}
    _yaml_file(Path("memhub.yaml"), {
        "project": "p", "db": "postgresql://u:topsecret@h/d",
        "llm": {"extractor": {"provider": "openai", "model": "x", "params": secrets}, "judge": {"provider": "openai", "model": "y"}},
    })
    result = runner.invoke(cli.app, ["config", "show", "--all"])
    assert result.exit_code == 0, result.output
    for literal in (*secrets.values(), "topsecret"):
        assert literal not in result.output
    shown = {k: v for k, v, _ in _lines(result.output)}
    for key in secrets:
        assert shown[f"llm.extractor.params.{key}"] == "***"
    assert shown["db"] == "postgresql://u:***@h/d"


def test_config_set_writes_validated_value_keeps_refs():
    _yaml_file(Path("memhub.yaml"), {"project": "p", "db": "postgresql://u:${PGPASSWORD}@h/d"})
    assert runner.invoke(cli.app, ["config", "set", "ingestion.max_candidates", "7"]).exit_code == 0
    assert runner.invoke(cli.app, ["config", "set", "scopes", "[user, workspace]"]).exit_code == 0
    assert runner.invoke(cli.app, ["config", "set", "ttl.temporary", "7d"]).exit_code == 0
    data = _loaded()
    assert data["ingestion"]["max_candidates"] == 7 and isinstance(data["ingestion"]["max_candidates"], int)
    assert data["scopes"] == ["user", "workspace"] and data["ttl"] == {"temporary": "7d"}
    assert data["db"] == "postgresql://u:${PGPASSWORD}@h/d" and data["project"] == "p"


def test_config_set_invalid_or_missing_yaml():
    result = runner.invoke(cli.app, ["config", "set", "ingestion.max_candidates", "5"])
    assert result.exit_code == 1 and "memhub init" in result.output  # no yaml anywhere
    path = _yaml_file(Path("memhub.yaml"), {"project": "p"})
    before = path.read_bytes()
    result = runner.invoke(cli.app, ["config", "set", "ingestion.max_candidates", "abc"])
    assert result.exit_code == 1 and "ingestion.max_candidates" in result.output and path.read_bytes() == before


@pytest.mark.parametrize("key", ["password", "api_key", "token", "secret"])
def test_config_set_refuses_literal_secret(key):
    path = _yaml_file(Path("memhub.yaml"), {"project": "p"})
    before = path.read_bytes()
    result = runner.invoke(cli.app, ["config", "set", f"models.{key}", "sk-123"])
    assert result.exit_code == 1 and "pass a ${VAR} reference" in result.output and path.read_bytes() == before
    assert runner.invoke(cli.app, ["config", "set", f"models.{key}", "${OPENAI_API_KEY}"]).exit_code == 0
    assert _loaded()["models"][key] == "${OPENAI_API_KEY}"


# --- doctor -----------------------------------------------------------------------------------------------------


class PingChat:
    def invoke(self, text):
        return "pong"


class BrokenChat:
    def invoke(self, text):
        raise RuntimeError("quota")


def _ledger(dsn: str, prefix: str, dims: int = 16) -> None:
    Memory(db=dsn, embedder=FakeEmbeddings(dims), config={"project": prefix}).init()


def _doctor(prefix: str, **entries):
    connect_apps.reset(**entries)
    _yaml_file(Path("memhub.yaml"), {"project": prefix, "connect": CONNECT})
    result = runner.invoke(cli.app, ["doctor"])
    return result, {c: (s, d) for s, c, d in _lines(result.output)}


def test_doctor_prints_checks_and_exit_code(pg_dsn):
    p = _prefix()
    _ledger(pg_dsn, p)
    result, lines = _doctor(p, db=pg_dsn, embedder=FakeEmbeddings(16), llm=PingChat())
    assert result.exit_code == 0, result.output
    assert [c for _, c, _ in _lines(result.output)] == ["config", "db", "chat", "embedder", "dims", "schema", "version"]  # version: PR 6 (AD-009)
    assert all(s == "ok" for s, _ in lines.values())
    result, lines = _doctor(p, db=pg_dsn, embedder=FakeEmbeddings(16), llm=BrokenChat())
    assert result.exit_code == 1 and lines["chat"][0] == "fail" and "quota" in lines["chat"][1]


def test_doctor_db_down_skips_dependents():
    result, lines = _doctor(_prefix(), db="postgresql://nobody@127.0.0.1:1/none", embedder=FakeEmbeddings(16))
    assert result.exit_code == 1
    assert lines["db"][0] == "fail" and lines["dims"][0] == "skip" and lines["schema"][0] == "skip"
    assert lines["embedder"][0] == "ok"


def test_doctor_dims_mismatch(pg_dsn):
    p = _prefix()
    _ledger(pg_dsn, p, dims=8)
    result, lines = _doctor(p, db=pg_dsn, embedder=FakeEmbeddings(16))
    assert result.exit_code == 1
    assert lines["embedder"][0] == "ok" and "16" in lines["embedder"][1]
    status, detail = lines["dims"]
    assert status == "fail" and "16" in detail and "8" in detail and "memhub reembed" in detail


def test_doctor_chat_check_and_none_configured(pg_dsn):
    p = _prefix()
    _ledger(pg_dsn, p)
    result, lines = _doctor(p, db=pg_dsn, embedder=FakeEmbeddings(16))
    assert result.exit_code == 0 and lines["chat"][0] == "skip" and "ingest" in lines["chat"][1]
    result, lines = _doctor(p, db=pg_dsn, embedder=FakeEmbeddings(16), llm=PingChat())
    assert lines["chat"][0] == "ok"


def test_doctor_schema_missing(pg_dsn):
    result, lines = _doctor(_prefix(), db=pg_dsn, embedder=FakeEmbeddings(16))  # no tables for this prefix
    assert result.exit_code == 1 and lines["schema"][0] == "fail" and "memhub init" in lines["schema"][1]
    p = _prefix()
    _ledger(pg_dsn, p)
    _, lines = _doctor(p, db=pg_dsn, embedder=FakeEmbeddings(16))
    assert lines["schema"][0] == "ok"


def test_doctor_pool_lifetime_warning(pg_dsn):
    p = _prefix()
    _ledger(pg_dsn, p)
    pool = ConnectionPool(pg_dsn, min_size=1, max_size=2, open=True)  # psycopg's default max_lifetime is 3600
    try:
        for life, warns in ((3600.0, True), (None, True), (0, True), (3000.0, False)):
            pool.max_lifetime = life
            result, lines = _doctor(p, db=pool, embedder=FakeEmbeddings(16))
            assert result.exit_code == 0, result.output
            assert ("pool" in lines) is warns, life
            if warns:
                assert lines["pool"][0] == "warn" and str(life) in lines["pool"][1] and "3600" in lines["pool"][1]
        conn = psycopg.connect(pg_dsn)
        try:
            for db in (pg_dsn, conn):  # a URL or a single connection: nothing to say about a pool
                result, lines = _doctor(p, db=db, embedder=FakeEmbeddings(16))
                assert "pool" not in lines and result.exit_code == 0
        finally:
            conn.close()
    finally:
        pool.close()


# --- use, context, -c -------------------------------------------------------------------------------------------


def test_use_writes_context():
    result = runner.invoke(cli.app, ["use", "acme", "--user", "anna"])
    assert result.exit_code == 0 and "workspace=acme user=anna" in result.output
    assert context_path() == Path.home() / ".config" / "memhub" / "context"
    assert json.loads(context_path().read_text()) == {"workspace": "acme", "user": "anna"}
    assert runner.invoke(cli.app, ["use", "acme"]).exit_code == 0
    assert json.loads(context_path().read_text()) == {"workspace": "acme", "user": None}


class Recorder:
    """A service that records the ids each command passes it."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def call(*a, **k):
            self.calls.append((name, a, k))
            return SimpleNamespace(terms=[], text="") if name == "expansion" else []
        return call


def _ids_of(command: list[str], rec: Recorder) -> tuple:
    name, a, k = [c for c in rec.calls if c[0] in ("add", "list", "search", "queue", "areas", "pages")][-1]
    return (k.get("workspace_id"), a[0] if name == "pages" else k.get("user_id"))


COMMANDS = {
    "add": ["add", "--type", "fact", "--scope", "user", "-f", "m.json"],
    "list": ["list"], "search": ["search", "q"], "queue": ["queue"], "areas": ["areas"], "page": ["page"],
}


def test_context_precedence_flag_env_file_default(monkeypatch):
    Path("m.json").write_text('{"content": "x"}')
    rec = Recorder()
    monkeypatch.setattr(cli, "_service", lambda config, **kw: rec)
    runner.invoke(cli.app, ["use", "acme", "--user", "anna"])
    for name, cmd in COMMANDS.items():
        rec.calls.clear()
        # env beats the context file; the file fills what env leaves
        r = runner.invoke(cli.app, cmd, env={"MEMHUB_USER": "bob"})
        assert r.exit_code == 0, (name, r.output)
        ws, user = _ids_of(cmd, rec)
        assert (ws, user if name != "queue" else "bob") == ("acme", "bob"), name
        rec.calls.clear()
        r = runner.invoke(cli.app, [*cmd, "--ws", "flagws", *([] if name == "queue" else ["--user", "carol"])], env={"MEMHUB_USER": "bob", "MEMHUB_WORKSPACE": "envws"})
        assert r.exit_code == 0, (name, r.output)
        ws, user = _ids_of(cmd, rec)
        assert (ws, user if name != "queue" else "carol") == ("flagws", "carol"), name
        rec.calls.clear()
        runner.invoke(cli.app, cmd, env={"MEMHUB_WORKSPACE": "envws"})
        assert _ids_of(cmd, rec)[0] == "envws", name
    context_path().unlink()  # no flag, no env, no file: the service falls back on the yaml's workspace_default
    for name, cmd in COMMANDS.items():
        rec.calls.clear()
        assert runner.invoke(cli.app, cmd).exit_code == 0
        assert _ids_of(cmd, rec) == (None, None), name


def test_real_add_uses_context_then_workspace_default(pg_dsn, monkeypatch):
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(cfg.dims))
    _yaml_file(Path("memhub.yaml"), _legacy_yaml(pg_dsn, _prefix()))
    assert runner.invoke(cli.app, ["init"]).exit_code == 0
    Path("m.json").write_text('{"content": "likes tea"}')
    row = json.loads(runner.invoke(cli.app, ["add", "--type", "fact", "--scope", "user", "--user", "zed", "-f", "m.json"]).stdout)
    assert row["workspace_id"] == "ws"  # the yaml's workspace_default
    runner.invoke(cli.app, ["use", "acme", "--user", "anna"])
    row = json.loads(runner.invoke(cli.app, ["add", "--type", "fact", "--scope", "user", "-f", "m.json"]).stdout)
    assert (row["workspace_id"], row["user_id"]) == ("acme", "anna")


def test_delete_user_never_from_context(pg_dsn, monkeypatch):
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(cfg.dims))
    _yaml_file(Path("memhub.yaml"), _legacy_yaml(pg_dsn, _prefix()))
    assert runner.invoke(cli.app, ["init"]).exit_code == 0
    runner.invoke(cli.app, ["use", "acme", "--user", "anna"])
    Path("m.json").write_text('{"content": "likes tea"}')
    assert runner.invoke(cli.app, ["add", "--type", "fact", "--scope", "user", "-f", "m.json"]).exit_code == 0
    for env in ({}, {"MEMHUB_USER": "anna"}):
        result = runner.invoke(cli.app, ["delete"], env=env)
        assert result.exit_code == 1 and "pass either an id or --user" in result.output
    rows = json.loads(runner.invoke(cli.app, ["list"]).stdout)
    assert len(rows) == 1  # nothing was erased


def test_bad_context_file_is_error(monkeypatch):
    monkeypatch.setattr(cli, "_service", lambda config, **kw: Recorder())
    context_path().parent.mkdir(parents=True)
    context_path().write_text("not json")
    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 1 and str(context_path()) in result.output


def _all_commands(uid: str) -> dict[str, list[str]]:
    Path("m.json").write_text('{"content": "x"}')
    return {
        "reembed": ["reembed"], "ingest": ["ingest", "--source", "nope"], "add": COMMANDS["add"], "list": ["list"],
        "search": ["search", "q"], "queue": ["queue"], "approve": ["approve", uid], "reject": ["reject", uid],
        "edit": ["edit", uid, "-f", "m.json"], "history": ["history", uid], "archive": ["archive", uid],
        "delete": ["delete", uid], "promote": ["promote", uid], "page": ["page"], "runs": ["runs"], "areas": ["areas"],
        "areas merge": ["areas", "merge", uid, uid], "config show": ["config", "show"],
        "config set": ["config", "set", "project", "x1"], "doctor": ["doctor"], "migrate": ["migrate"], "mcp": ["mcp"],
    }


def test_c_optional_discovery_and_no_yaml_message(pg_dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(cfg.dims))
    uid = str(uuid.uuid4())
    commands = _all_commands(uid)
    registered = {c.name or c.callback.__name__ for c in cli.app.registered_commands} | {"areas", "config"}
    assert {c.split()[0] for c in commands} >= registered - {"init", "use"}  # every command is in the table
    for name, cmd in commands.items():  # no yaml anywhere
        result = runner.invoke(cli.app, cmd)
        assert result.exit_code == 1, name
        assert "./memhub.yaml" in result.output and "$MEMHUB_CONFIG" in result.output and "memhub init" in result.output, name
    good = _yaml_file(tmp_path / "good.yaml", _legacy_yaml(pg_dsn, _prefix()))
    assert runner.invoke(cli.app, ["init", "-c", str(good)]).exit_code == 0
    _yaml_file(Path("memhub.yaml"), _legacy_yaml(pg_dsn, _prefix()))  # a discovered yaml whose ledger does not exist
    assert runner.invoke(cli.app, ["list"]).exit_code == 1  # found, and used: it is the ledger that is missing
    assert "no memhub.yaml found" not in runner.invoke(cli.app, ["list"]).output
    result = runner.invoke(cli.app, ["list", "-c", str(good)])  # -c wins over the file in ./
    assert result.exit_code == 0 and json.loads(result.stdout) == []
    _yaml_file(Path("memhub.yaml"), _legacy_yaml(pg_dsn, _prefix()))
    shared = _legacy_yaml(pg_dsn, "p" + uuid.uuid4().hex[:8])
    _yaml_file(Path("memhub.yaml"), shared)
    assert runner.invoke(cli.app, ["init"]).exit_code == 0
    for name, cmd in commands.items():  # now every command finds the discovered yaml
        if name in ("config set", "doctor", "mcp"):  # mcp would serve on stdin
            continue
        result = runner.invoke(cli.app, cmd)
        assert "no memhub.yaml found" not in result.output and "config file not found" not in result.output, (name, result.output)


def test_discovered_yaml_folder_resolves_source_paths(pg_dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(cfg.dims))
    monkeypatch.setattr(cli, "build_chat_model", lambda cfg: FakeChatModel())
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "data.jsonl").write_text("")
    data = _legacy_yaml(pg_dsn, _prefix())
    data["sources"] = {"jsonl": {"kind": "jsonl", "path": "data.jsonl", "one_line_per": "turn", "fields": {
        "thread_id": "chat_id", "user_id": "chat_id", "message_id": "message_id", "timestamp": "timestamp",
        "user_content": "q", "assistant_content": "a"}}}
    named = _yaml_file(folder / "elsewhere.yaml", data)
    elsewhere = _cwd(tmp_path, monkeypatch, "cwd")
    assert not (elsewhere / "data.jsonl").exists()
    monkeypatch.setenv("MEMHUB_CONFIG", str(named))  # discovered through $MEMHUB_CONFIG, in another folder
    assert runner.invoke(cli.app, ["init"]).exit_code == 0
    result = runner.invoke(cli.app, ["ingest", "--source", "jsonl", "--dry-run"])
    assert result.exit_code == 0, result.output


def test_cli_without_embedder_source_names_key(pg_dsn):
    data = _legacy_yaml(pg_dsn, _prefix())
    del data["embeddings"]
    path = _yaml_file(Path("memhub.yaml"), data)
    for command in (["list"], ["init"], ["search", "x"]):
        result = runner.invoke(cli.app, [*command, "--config", str(path)])
        assert result.exit_code != 0 and "embeddings" in result.output, command


def test_config_set_follows_the_files_spelling_of_an_alias():
    path = _yaml_file(Path("memhub.yaml"), {"project_prefix": "a"})
    assert runner.invoke(cli.app, ["config", "set", "project", "b"]).exit_code == 0
    assert _loaded() == {"project_prefix": "b"}
    _yaml_file(path, {"project": "a"})
    assert runner.invoke(cli.app, ["config", "set", "project_prefix", "c"]).exit_code == 0
    assert _loaded() == {"project": "c"}


def test_config_show_masks_a_password_that_holds_an_at_sign():
    _yaml_file(Path("memhub.yaml"), {"project": "p", "db": "postgresql://u:p@ss@h/d"})
    shown = {k: v for k, v, _ in _lines(runner.invoke(cli.app, ["config", "show"]).output)}
    assert shown["db"] == "postgresql://u:***@h/d"
