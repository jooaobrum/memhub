"""PR 4 demo: `memhub init --db sqlite:///m.db`, ingest a JSONL log, approve the workspace candidate, search it -
no Docker, no Postgres (every way into psycopg raises)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
import yaml
from typer.testing import CliRunner

from memhub import cli
from memhub.pipeline.extract import Candidate, Evidence
from tests.fakes import FakeChatModel, FakeEmbeddings, extraction

runner = CliRunner()


@pytest.fixture
def no_postgres(monkeypatch):
    def refuse(*_a, **_kw):
        raise AssertionError("the SQLite demo must not open a Postgres connection")

    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(lambda cls, *a, **kw: refuse()))


def _cand(content: str, quote: str, mid: str, scope: str) -> Candidate:
    return Candidate(type="fact", scope=scope, fields={"content": content}, utility=5, applies_generally=True,
                     assertion="stated", evidence=[Evidence(message_id=mid, quote=quote, claim_source="user")])


def test_init_ingest_approve_search_on_sqlite_without_postgres(no_postgres, tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.chdir(app)
    model = FakeChatModel([extraction(
        _cand("Pump P4 cavitates below half-open suction", "Pump P4 cavitates when the suction valve is under half open", "m1", "workspace"),
    )], unrelated_by_default=True)  # one segment, one extraction call (the support preset has no glossary type)
    import langchain.chat_models
    import langchain.embeddings

    monkeypatch.setattr(langchain.embeddings, "init_embeddings", lambda spec, **kw: FakeEmbeddings(16))  # `models:` strings
    monkeypatch.setattr(langchain.chat_models, "init_chat_model", lambda spec, **kw: model)

    init = runner.invoke(cli.app, ["init", "--db", "sqlite:///m.db", "--embeddings", "openai:text-embedding-3-small",
                                   "--chat", "openai:gpt-4.1-mini", "--preset", "support", "--project", "plant"])
    assert init.exit_code == 0, init.output
    assert (app / "m.db").exists() and yaml.safe_load((app / "memhub.yaml").read_text())["db"] == "sqlite:///m.db"

    said = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    (app / "log.jsonl").write_text(json.dumps({"chat_id": "c1", "user_id": "u1", "message_id": "m1", "timestamp": said,
        "q": "Pump P4 cavitates when the suction valve is under half open", "a": "Noted"}) + "\n")
    config = yaml.safe_load((app / "memhub.yaml").read_text())
    config["sources"] = {"log": {"kind": "jsonl", "path": "log.jsonl", "one_line_per": "turn", "fields": {
        "thread_id": "chat_id", "user_id": "user_id", "message_id": "message_id", "timestamp": "timestamp",
        "user_content": "q", "assistant_content": "a"}}}
    (app / "memhub.yaml").write_text(yaml.safe_dump(config))

    ingest = runner.invoke(cli.app, ["ingest", "--source", "log"])
    assert ingest.exit_code == 0, ingest.output
    assert json.loads(ingest.stdout)["created"] == 1 and model.queue == []

    ws = config.get("workspace_default") or "default"
    queue = runner.invoke(cli.app, ["queue", "--ws", ws])
    assert queue.exit_code == 0, queue.output
    [candidate] = json.loads(queue.stdout)
    assert candidate["status"] == "candidate" and candidate["scope"] == "workspace"

    approve = runner.invoke(cli.app, ["approve", candidate["id"]])
    assert approve.exit_code == 0, approve.output
    assert json.loads(approve.stdout)["status"] == "active"

    found = runner.invoke(cli.app, ["search", "pump P4 suction valve", "--ws", ws, "--user", "u1"])
    assert found.exit_code == 0, found.output
    hits = json.loads(found.stdout)
    assert hits[0]["content"] == "Pump P4 cavitates below half-open suction" and hits[0]["status"] == "active"
