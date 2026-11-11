"""PR 2 demo: `memhub init` in an empty folder, then `add` and `search` with no `-c`, and `Memory()` on the same yaml."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import yaml
from typer.testing import CliRunner

from memhub import Memory, cli

runner = CliRunner()


def test_pr2_demo(pg_dsn, tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.chdir(app)
    monkeypatch.setenv("PR2_DSN", pg_dsn)
    project = f"e{uuid.uuid4().hex[:8]}"
    assert list(app.iterdir()) == []

    init = runner.invoke(cli.app, ["init", "--preset", "support", "--project", project, "--connect", "tests.e2e.pr2_app:build"])
    assert init.exit_code == 0, init.output
    written = yaml.safe_load((app / "memhub.yaml").read_text())
    assert written["connect"] == "tests.e2e.pr2_app:build" and written["preset"] == "support" and "db" not in written

    fact = app / "fact.json"
    fact.write_text(json.dumps({"content": "Compressor C3 trips when the inlet filter is clogged"}))
    added = runner.invoke(cli.app, ["add", "--type", "fact", "--scope", "workspace", "--ws", "acme", "-f", str(fact)])  # no -c
    assert added.exit_code == 0, added.output
    row = json.loads(added.stdout)
    assert row["status"] == "active" and row["workspace_id"] == "acme"

    found = runner.invoke(cli.app, ["search", "compressor C3 trips", "--ws", "acme"])  # no -c
    assert found.exit_code == 0, found.output
    assert [r["memory_id"] for r in json.loads(found.stdout)][:1] == [row["memory_id"]]

    m = Memory()  # the same yaml, from Python
    hits = m.search("compressor C3 trips", workspace_id="acme", user_id="anna", run_id="run-1")
    assert [str(h["memory_id"]) for h in hits][:1] == [row["memory_id"]]
