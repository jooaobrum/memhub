"""PR 6 demo: a database created by memhub 0.1.0 is refused by `Memory`, upgraded by `memhub migrate`, then used."""
from __future__ import annotations

import uuid

import pytest
import yaml
from typer.testing import CliRunner

from memhub import Memory, SchemaOutdated, cli
from tests.fakes import FakeEmbeddings
from tests.test_migrations import load_v0_1_0

IDS = dict(workspace_id="acme", user_id="anna", run_id="run-1")


def test_v0_1_0_ledger_refused_migrated_then_used(pg_dsn, tmp_path, monkeypatch):
    prefix = f"v{uuid.uuid4().hex[:8]}"
    load_v0_1_0(pg_dsn, prefix, model="fake-embed")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(cfg.dims))
    (tmp_path / "memhub.yaml").write_text(yaml.safe_dump({
        "project_prefix": prefix, "database_url": pg_dsn, "workspace_default": "acme",
        "embeddings": {"provider": "openai", "model": "fake-embed", "api_key_env": "UNSET", "dims": 16},
        "scopes": ["user", "workspace"], "types": {"fact": {"class": "memhub.types:Fact", "type_prior": 0.6}},
    }))

    def memory() -> Memory:
        return Memory(embedder=FakeEmbeddings(16))  # db and config from the memhub.yaml found in ./

    with pytest.raises(SchemaOutdated, match="run `memhub migrate`"):
        memory().search("inlet filter", **IDS)

    result = CliRunner().invoke(cli.app, ["migrate"])
    assert result.exit_code == 0, result.output
    assert "schema version 1 -> 2" in result.output

    m = memory()
    added = m.add("Compressor C4 needs a new belt", run_id="run-2", workspace_id="acme", user_id="anna").results[0]
    assert m.get(added.id, **IDS)["run_id"] == "run-2"
    hits = m.search("compressor C3 trips when the inlet filter is clogged", **IDS)
    assert hits[0]["content"] == "Compressor C3 trips when the inlet filter is clogged"
    assert str(hits[0]["memory_id"]) == "6f1d0c9e-0000-4000-8000-000000000002"
