"""Adapting memhub to another project: its own schemas, database, source and models, with no change to memhub itself."""
from __future__ import annotations

import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
import yaml

from memhub.config import ConfigError, SourceConfig, load_config
from memhub.store import MemoryStore
from tests.test_config import _base_config, _write_yaml
from tests.test_ingest_e2e import env  # noqa: F401  (a fixture)


def _config(tmp_path, monkeypatch, **extra):
    monkeypatch.setenv("MEMHUB_DATABASE_URL", "postgresql://localhost/acme")
    data = {**_base_config(), **extra}
    return load_config(_write_yaml(tmp_path, data))


# --- schemas ------------------------------------------------------------------------------------------------------

def test_a_type_declared_in_the_yaml_needs_no_python(tmp_path, monkeypatch):
    settings = _config(tmp_path, monkeypatch, types={"incident": {
        "fields": {"equipment": {"type": "str", "description": "the machine"}, "fault_code": "str", "fix": "str?"},
        "content_template": "{equipment} {fault_code}: {fix}"}})
    cls = settings.build_registry().latest("incident")
    assert list(cls.model_fields) == ["content", "entities", "tags", "equipment", "fault_code", "fix"]
    assert cls.model_fields["equipment"].description == "the machine" and cls.model_fields["fix"].default is None
    with pytest.raises(Exception):
        cls.model_validate({"content": "x", "equipment": "P-1"})  # fault_code is required


def test_an_unknown_field_type_or_a_type_with_no_schema_is_refused(tmp_path, monkeypatch):
    settings = _config(tmp_path, monkeypatch, types={"bad": {"fields": {"x": "datetime"}}})
    with pytest.raises(ValueError, match="unknown type"):
        settings.build_registry()
    with pytest.raises(Exception, match="needs `class`"):
        _config(tmp_path, monkeypatch, types={"bad": {"retrieval": "search"}})


def test_a_project_class_next_to_the_yaml_is_importable(tmp_path, monkeypatch):
    (tmp_path / "proj_types.py").write_text(textwrap.dedent('''
        from memhub.types import MemoryBase
        class Ticket(MemoryBase):
            symptom: str
    '''))
    settings = _config(tmp_path, monkeypatch, types={"ticket": {"class": "proj_types:Ticket"}})
    assert "symptom" in settings.build_registry().latest("ticket").model_fields


# --- database -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["my-app", "Acme Corp", "x; drop table users; --", "1abc", ""])
def test_a_prefix_that_is_not_a_plain_identifier_is_refused(tmp_path, monkeypatch, prefix):
    with pytest.raises(Exception, match="letters, digits and underscores|at least"):
        _config(tmp_path, monkeypatch, project_prefix=prefix)


def test_the_ledger_can_live_in_its_own_schema_of_an_existing_database(pg_dsn):
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute("CREATE TABLE IF NOT EXISTS legacy_customers (id int)")
    store = MemoryStore(pg_dsn, f"p{uuid.uuid4().hex[:6]}", schema="memhub_ns")
    store.init(embedding_model="m", dims=8)
    store.init(embedding_model="m", dims=8)  # idempotent
    with psycopg.connect(pg_dsn) as c:
        where = {r[0] for r in c.execute("SELECT table_schema FROM information_schema.tables WHERE table_name LIKE %s", (f"{store.prefix}_%",))}
        assert where == {"memhub_ns"}
        assert c.execute("SELECT count(*) FROM legacy_customers").fetchone()[0] == 0  # the project's own tables are untouched
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {store._t('memory')}")
        assert cur.fetchone()["n"] == 0


def test_a_second_worker_cannot_take_a_thread_that_is_held(store):
    with store.thread_lock("jsonl", "t1") as first, store.thread_lock("jsonl", "t1") as second, store.thread_lock("jsonl", "t2") as other:
        assert (first, second, other) == (True, False, True)
    with store.thread_lock("jsonl", "t1") as again:
        assert again is True  # released with the connection


# --- sources ------------------------------------------------------------------------------------------------------

def test_the_projects_own_table_is_a_source_with_a_tenant_per_row(pg_dsn, monkeypatch):
    from memhub.sources.sql import SQLSource

    table = f"chats_{uuid.uuid4().hex[:6]}"
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute(f"CREATE TABLE {table} (chat text, msg text, tenant text, cust text, at timestamptz, q text, a text)")
        c.execute(f"INSERT INTO {table} VALUES ('c1','m1','acme','anna',%s,'I use Okta','ok'),('c2','m2','globex','gina',%s,'VAT is FR999',NULL),(NULL,'m3','x','y',%s,'bad',NULL)",
                  (datetime(2026, 9, 1, tzinfo=timezone.utc),) * 3)
    monkeypatch.setenv("CRM_URL", pg_dsn)
    cfg = SourceConfig(kind="sql", dsn_env="CRM_URL", query=f"SELECT * FROM {table} ORDER BY at, msg", fields={
        "thread_id": "chat", "message_id": "msg", "timestamp": "at", "user_id": "cust", "workspace_id": "tenant",
        "user_content": "q", "assistant_content": "a"})
    source = SQLSource(cfg, workspace_id="default")
    got = [(i.workspace_id, i.user_id, i.role, i.message_id) for i in source.read()]
    assert got == [("acme", "anna", "user", "m1"), ("acme", "anna", "assistant", "m1:a"), ("globex", "gina", "user", "m2")]
    assert source.skipped == 1  # the row with no thread id


def test_the_projects_own_adapter_is_named_in_the_yaml(tmp_path):
    from memhub.cli import _build_source

    (tmp_path / "my_adapter.py").write_text(textwrap.dedent('''
        class Zendesk:
            def __init__(self, cfg, *, workspace_id):
                self.cfg, self.workspace_id, self.skipped = cfg, workspace_id, 0
            def read(self):
                return iter(())
    '''))
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        settings = load_config_from_dict(tmp_path, {"sources": {"zd": {"kind": "my_adapter:Zendesk", "subdomain": "acme"}}})
        source = _build_source(settings, "zd", tmp_path / "memhub.yaml")
    finally:
        sys.path.remove(str(tmp_path))
    assert type(source).__name__ == "Zendesk" and source.cfg.subdomain == "acme" and source.workspace_id == "acme"


def load_config_from_dict(tmp_path, extra):
    import os
    os.environ.setdefault("MEMHUB_DATABASE_URL", "postgresql://localhost/acme")
    return load_config(_write_yaml(tmp_path, {**_base_config(), **extra}))


# --- models -------------------------------------------------------------------------------------------------------

def test_models_are_configuration_azure_and_gateways_included(monkeypatch):
    from memhub.config import EmbeddingConfig, ModelConfig, build_chat_model, build_embeddings

    monkeypatch.setenv("K", "sk-test")
    chat = build_chat_model(ModelConfig(provider="azure_openai", model="gpt-4o", api_key_env="K",
                                        params={"azure_endpoint": "https://x.openai.azure.com", "api_version": "2024-06-01", "azure_deployment": "d"}))
    assert type(chat).__name__ == "AzureChatOpenAI"
    gateway = build_embeddings(EmbeddingConfig(provider="openai", model="text-embedding-3-small", api_key_env="K", dims=8, base_url="https://gw/v1"))
    assert gateway.openai_api_base == "https://gw/v1"  # was silently ignored before
    azure = build_embeddings(EmbeddingConfig(provider="azure_openai", model="text-embedding-3-small", api_key_env="K", dims=8,
                                             base_url="https://x.openai.azure.com", params={"api_version": "2024-06-01", "azure_deployment": "e"}))
    assert type(azure).__name__ == "AzureOpenAIEmbeddings" and azure.azure_endpoint == "https://x.openai.azure.com"


def test_a_factory_builds_the_model_when_a_yaml_entry_is_not_enough(tmp_path, monkeypatch):
    from memhub.config import ModelConfig, build_chat_model, build_embeddings, EmbeddingConfig

    (tmp_path / "my_models.py").write_text("def chat(cfg):\n    return ('chat', cfg.model)\ndef emb(cfg):\n    return ('emb', cfg.dims)\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert build_chat_model(ModelConfig(model="gpt-4o", factory="my_models:chat")) == ("chat", "gpt-4o")  # no provider, no key needed
    assert build_embeddings(EmbeddingConfig(model="e", dims=8, factory="my_models:emb")) == ("emb", 8)


def test_a_type_can_skip_the_admin_review_for_workspace_scope(env):
    from memhub.config import TypeConfig
    from tests.test_ingest_e2e import cand, extraction, line
    from tests.fakes import FakeChatModel

    env.settings.types["fact"] = TypeConfig(**{"class": "memhub.types:Fact"}, review=False, scopes=["user", "workspace"])
    env.write(line())
    env.run(FakeChatModel([extraction(cand(scope="workspace"))]))
    [m] = env.memories()
    assert (m["scope"], m["status"]) == ("workspace", "active")
