"""`connect:`, `models:` and `Memory()`: the app's own objects, or built from the config."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import yaml
from psycopg_pool import ConnectionPool
from typer.testing import CliRunner

from memhub import Memory, cli
from memhub.config import ConfigError, EmbeddingConfig
from memhub.settings import Parts, resolve
from tests import connect_apps
from tests.fakes import FakeChatModel, FakeEmbeddings

IDS = {"workspace_id": "ws", "user_id": "alice", "run_id": "run-1"}
CONNECT = "tests.connect_apps:build"


@pytest.fixture(autouse=True)
def _fresh_state():
    connect_apps.reset()
    yield
    connect_apps.reset()


def _yaml(data: dict, folder: Path | None = None) -> Path:
    path = (folder or Path.cwd()) / "memhub.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _prefix() -> str:
    return f"k{uuid.uuid4().hex[:8]}"


def test_connect_factory_called_once_with_no_args_and_objects_used(tmp_path):
    db, emb, llm, judge = "postgresql://u@h/d", FakeEmbeddings(16), FakeChatModel(), FakeChatModel()
    connect_apps.reset(db=db, embedder=emb, llm=llm, judge=judge)
    parts = Parts(resolve({"connect": CONNECT}, environ={}))
    assert (parts.db, parts.embedder, parts.llm, parts.judge) == (db, emb, llm, judge)
    assert connect_apps.CALLS == [()]  # once, with no arguments
    # a function that sits next to the yaml is found from the yaml's folder
    (tmp_path / "appfn_c18.py").write_text("def build():\n    return {'db': 'postgresql://x/y', 'embedder': 'E'}\n")
    path = _yaml({"connect": "appfn_c18:build"}, tmp_path)
    try:
        local = Parts(resolve(path, environ={}))
        assert (local.db, local.embedder) == ("postgresql://x/y", "E")
    finally:
        sys.modules.pop("appfn_c18", None)
        sys.path[:] = [p for p in sys.path if p != str(tmp_path.resolve())]


@pytest.mark.parametrize("spec, entries, match", [
    ("nocolon", {"db": "x", "embedder": "e"}, "connect.*module:function"),
    ("os:nothing", {"db": "x", "embedder": "e"}, "connect.*nothing"),
    ("tests.connect_apps:NOT_CALLABLE", {"db": "x", "embedder": "e"}, "connect.*not callable"),
    (CONNECT, {"return": ["not", "a", "dict"]}, "connect.*dict"),
    (CONNECT, {"db": "x"}, "connect.*`embedder`"),
])
def test_connect_misuse_is_config_error(spec, entries, match):
    connect_apps.reset(**entries)
    parts = Parts(resolve({"connect": spec}, environ={}))
    with pytest.raises(ConfigError, match=match):
        parts.db
    with pytest.raises(ConfigError, match=match):
        parts.embedder


def test_models_strings_build_through_langchain_inits(monkeypatch):
    import langchain.chat_models as chat_mod
    import langchain.embeddings as emb_mod

    chats, embs = [], []
    monkeypatch.setattr(emb_mod, "init_embeddings", lambda spec, **kw: embs.append(spec) or FakeEmbeddings(16))
    monkeypatch.setattr(chat_mod, "init_chat_model", lambda spec, **kw: chats.append(spec) or FakeChatModel())
    cfg = {"db": "postgresql://u@h/d", "models": {"chat": "openai:gpt-x", "embeddings": "openai:emb-y"}}
    parts = Parts(resolve(cfg, environ={}))
    assert parts.db == "postgresql://u@h/d"
    assert isinstance(parts.embedder, FakeEmbeddings) and embs == ["openai:emb-y"]
    assert parts.judge is parts.llm and chats == ["openai:gpt-x"]  # the judge defaults to the chat model
    parts = Parts(resolve({**cfg, "models": {**cfg["models"], "judge": "openai:gpt-j"}}, environ={}))
    assert parts.judge is not parts.llm and "openai:gpt-j" in chats


def test_legacy_blocks_win_over_models(monkeypatch):
    import langchain.chat_models as chat_mod
    import langchain.embeddings as emb_mod

    def boom(*a, **k):
        raise AssertionError("models: must not be used when llm:/embeddings: blocks exist")

    monkeypatch.setattr(emb_mod, "init_embeddings", boom)
    monkeypatch.setattr(chat_mod, "init_chat_model", boom)
    cfg = {
        "db": "postgresql://u@h/d",
        "llm": {"extractor": {"provider": "openai", "model": "ex"}, "judge": {"provider": "openai", "model": "ju"}},
        "embeddings": {"provider": "openai", "model": "emb", "dims": 16},
        "models": {"chat": "openai:other", "embeddings": "openai:other"},
    }
    built = []
    parts = Parts(resolve(cfg, environ={}), build_chat=lambda c: built.append(c.model) or FakeChatModel(),
                  build_embed=lambda c: built.append(("emb", c.model)) or FakeEmbeddings(16))
    parts.llm, parts.judge, parts.embedder
    assert built == ["ex", "ju", ("emb", "emb")]


def test_missing_db_or_embedder_source_errors():
    parts = Parts(resolve({"project": "p"}, environ={}))
    with pytest.raises(ConfigError, match=r"connect.*db.*MEMHUB_DB"):
        parts.db
    with pytest.raises(ConfigError, match=r"connect.*models\.embeddings.*embeddings"):
        parts.embedder


def test_objects_passed_beat_connect_and_skip_it():
    connect_apps.reset(db="B", embedder="EB", llm="LB", judge="JB")
    resolved = resolve({"connect": CONNECT}, environ={})
    parts = Parts(resolved, passed={"db": "A", "embedder": "EA", "llm": "LA", "judge": "JA"})
    assert (parts.db, parts.embedder, parts.llm, parts.judge) == ("A", "EA", "LA", "JA")
    assert connect_apps.CALLS == []  # db and embedder passed: connect is never called
    parts = Parts(resolved, passed={"db": "A", "llm": "LA"})
    assert (parts.db, parts.llm, parts.embedder, parts.judge) == ("A", "LA", "EB", "LA")  # judge defaults to the passed llm
    assert connect_apps.CALLS == [()]
    emb = FakeEmbeddings(16)
    m = Memory(db="postgresql://a@h/d", embedder=emb, config={"connect": CONNECT})
    assert m.embedder is emb and connect_apps.CALLS == [()]  # still one: Memory did not call it


def test_memory_without_arguments_works_end_to_end(pg_dsn, monkeypatch):
    prefix = _prefix()
    connect_apps.reset(db=pg_dsn, embedder=FakeEmbeddings(16))
    _yaml({"project": prefix, "connect": CONNECT})
    m = Memory()
    m.init()
    event = m.add({"content": "The press needs the guard closed"}, type="fact", **IDS).results[0]
    assert [str(r["memory_id"]) for r in m.search("press guard", **IDS)] == [event.id]
    # the same, from `db:` and `models.embeddings`
    import langchain.embeddings as emb_mod

    monkeypatch.setattr(emb_mod, "init_embeddings", lambda spec, **kw: FakeEmbeddings(16))
    prefix2 = _prefix()
    _yaml({"project": prefix2, "db": pg_dsn, "models": {"embeddings": "fake:emb"}})
    m2 = Memory()
    m2.init()
    event2 = m2.add("Lathe 4 is down on Mondays", **IDS).results[0]
    assert [str(r["memory_id"]) for r in m2.search("lathe down", **IDS)] == [event2.id]


def test_memory_without_arguments_and_no_yaml_lists_places():
    with pytest.raises(ConfigError) as err:
        Memory()
    for place in ("./memhub.yaml", "$MEMHUB_CONFIG", "~/.config/memhub/memhub.yaml"):
        assert place in str(err.value)


def test_connect_db_left_open(pg_dsn):
    prefix = _prefix()
    pool = ConnectionPool(pg_dsn, min_size=1, max_size=2, open=True)
    try:
        connect_apps.reset(db=pool, embedder=FakeEmbeddings(16))
        _yaml({"project": prefix, "connect": CONNECT})
        m = Memory()
        m.init()
        m.add("a fact", **IDS)
        m.search("fact", **IDS)
        assert pool.closed is False
        result = CliRunner().invoke(cli.app, ["list"])
        assert result.exit_code == 0, result.output
        assert pool.closed is False
        with pool.connection() as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        pool.close()


def test_memory_accepts_overrides():
    m = Memory(db="postgresql://a@h/d", embedder=FakeEmbeddings(16),
               overrides={"scopes": ["workspace"], "types": {"incident": "{equipment}: {fix}"}})
    assert set(m.settings.types) == {"incident"} and m.settings.scopes == ["workspace"]
