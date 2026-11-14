"""`memhub approve ID --set KEY=VALUE` (issue #19, S1): the real typer app against a real pgvector Postgres."""
from __future__ import annotations

import json
import uuid

import pytest
import yaml
from typer.testing import CliRunner

from memhub import cli
from memhub.service import Actor
from tests.fakes import FakeEmbeddings
from tests.test_cli import _config_dict

runner = CliRunner()


@pytest.fixture
def env(pg_dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(dims=cfg.dims))
    config = _config_dict(pg_dsn, f"e{uuid.uuid4().hex[:8]}")
    config["types"]["reading"] = {"fields": {"count": "int"}}
    path = tmp_path / "memhub.yaml"
    path.write_text(yaml.safe_dump(config))
    assert runner.invoke(cli.app, ["init", "--config", str(path)]).exit_code == 0
    service = cli._service(path)

    def candidate() -> dict:
        return service.propose(Actor(id="agent"), type="reading", scope="workspace",
                               fields={"content": "Login uses Auth0", "count": 1}, evidence=[], workspace_id="ws")

    def approve(*args: str):
        return runner.invoke(cli.app, ["approve", *args, "--config", str(path)],
                             env={"MEMHUB_CLI_ROLES": "workspace_admin"})

    return service, candidate, approve


def test_approve_set_parses_json(env):
    service, candidate, approve = env
    c = candidate()
    result = approve(str(c["id"]), "--set", "content=Login uses Okta", "--set", "count=3")
    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)
    assert (row["status"], row["content"], row["payload"]["count"], row["version"]) == ("active", "Login uses Okta", 3, 2)
    active = service.store.unit_of_work
    with active() as uow:
        assert uow.memories.get_active(c["memory_id"])["payload"]["count"] == 3


@pytest.mark.parametrize("arg, message", [("content", "--set expects KEY=VALUE"), ("count=abc", "count")],
                         ids=["no-equals", "invalid"])
def test_approve_set_errors(env, arg, message):
    service, candidate, approve = env
    c = candidate()
    result = approve(str(c["id"]), "--set", arg)
    assert result.exit_code == 1
    assert message in result.stderr
    with service.store.unit_of_work() as uow:
        assert [(r["version"], r["status"]) for r in uow.memories.history(c["memory_id"])] == [(1, "candidate")]
