"""The `claude_code` source: what a Claude Code transcript line becomes, what is left out, and that a bad line costs
only itself. The fixture session in `tests/data/claude_code/` follows the line format of real transcripts."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memhub import cli
from memhub.config import SourceConfig
from memhub.sources.claude_code import WRAPPERS, ClaudeCodeSource
from tests.fakes import FakeChatModel, FakeEmbeddings, extraction

DATA = Path(__file__).parent / "data" / "claude_code"
SESSION = "5b7e0f7a-1111-4c2e-9d55-000000000001"
runner = CliRunner()


def _source(path: Path, **extra) -> ClaudeCodeSource:
    return ClaudeCodeSource(SourceConfig(kind="claude_code", path=str(path), **extra))


def test_messages_and_their_ids(monkeypatch):
    monkeypatch.setenv("MEMHUB_USER", "anna")
    got = list(_source(DATA).read())
    assert [(m.role, m.message_id, m.content) for m in got] == [
        ("user", "u1", "In this repo we use pnpm, never npm"),
        ("assistant", "a1", "Noted: pnpm for every install."),
        ("user", "u5", "Should errors use <b> tags? No, keep plain markdown"),
        ("assistant", "a4", "Plain markdown it is."),
    ]
    first = got[0]
    assert (first.thread_id, first.workspace_id, first.user_id, first.trace_id) == (SESSION, "-tmp-alpha", "anna", SESSION)
    assert first.timestamp == datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    assert all("the user wants pnpm" not in m.content for m in got)  # thinking is never kept


def _line(kind: str, uuid: str, content, **extra) -> str:
    return json.dumps({"type": kind, "sessionId": "s", "uuid": uuid, "timestamp": "2026-11-01T09:00:00Z",
                       "message": {"role": kind, "content": content}, **extra})


def test_what_is_left_out(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    lines = [
        _line("user", "meta", "injected", isMeta=True),
        _line("user", "side", "subagent prompt", isSidechain=True),
        json.dumps({"type": "assistant", "sessionId": "s", "uuid": "syn", "timestamp": "2026-11-01T09:00:00Z",
                    "message": {"role": "assistant", "model": "<synthetic>", "content": [{"type": "text", "text": "limit"}]}}),
        *(_line("user", f"w{i}", f"{prefix} rest of it") for i, prefix in enumerate(WRAPPERS)),
        _line("user", "kept", "keep this <tag> text"),
    ]
    (project / "s.jsonl").write_text("\n".join(lines) + "\n")
    source = _source(tmp_path)
    assert [m.message_id for m in source.read()] == ["kept"]
    assert source.skipped == 0 and len(WRAPPERS) == 6


def test_bad_lines_are_skipped_one_by_one(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "s.jsonl").write_text("\n".join([
        _line("user", "before", "first"),
        "{not json",
        "[1, 2, 3]",
        json.dumps({"type": "user", "uuid": "x", "timestamp": "2026-11-01T09:00:00Z", "message": {"content": "no session"}}),
        _line("user", "after", "still read"),
    ]) + "\n")
    source = _source(tmp_path)
    assert [m.message_id for m in source.read()] == ["before", "after"]
    assert source.skipped == 3


def test_project_globs_and_a_missing_folder(tmp_path, monkeypatch):
    for name in ("-work-alpha-api", "-work-beta"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "s.jsonl").write_text(_line("user", name, f"from {name}") + "\n")
    assert [m.workspace_id for m in _source(tmp_path, projects=["*alpha*"]).read()] == ["-work-alpha-api"]
    assert sorted(m.workspace_id for m in _source(tmp_path).read()) == ["-work-alpha-api", "-work-beta"]

    missing = tmp_path / "nowhere"
    with pytest.raises(ValueError, match=str(missing)):
        _source(missing)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(16))
    monkeypatch.setattr(cli, "build_chat_model", lambda cfg: FakeChatModel())
    _config(tmp_path, sources={"claude_code": {"kind": "claude_code", "path": str(missing)}})
    assert runner.invoke(cli.app, ["init"]).exit_code == 0
    result = runner.invoke(cli.app, ["ingest", "--source", "claude_code"])
    assert result.exit_code == 1 and str(missing) in result.output


def _config(folder: Path, **extra) -> None:
    import yaml

    (folder / "memhub.yaml").write_text(yaml.safe_dump({
        "project_prefix": "cc", "database_url": f"sqlite:///{folder / 'm.db'}", "workspace_default": "default",
        "llm": {"extractor": {"provider": "openai", "model": "x"}, "judge": {"provider": "openai", "model": "x"}},
        "embeddings": {"provider": "openai", "model": "fake-embed", "api_key_env": "UNSET", "dims": 16},
        "scopes": ["user", "workspace"], "types": {"fact": {"class": "memhub.types:Fact", "type_prior": 0.6}}, **extra,
    }))


def test_ingest_with_the_defaults_twice(tmp_path, monkeypatch):
    home = tmp_path / "home"
    shutil.copytree(DATA, home / ".claude" / "projects")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    model = FakeChatModel([extraction(), extraction()], unrelated_by_default=True)  # new messages, then the final pass
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(16))
    monkeypatch.setattr(cli, "build_chat_model", lambda cfg: model)
    _config(tmp_path)  # no `sources:` at all
    assert runner.invoke(cli.app, ["init"]).exit_code == 0
    first = runner.invoke(cli.app, ["ingest", "--source", "claude_code"])
    assert first.exit_code == 0, first.output
    summary, calls = json.loads(first.stdout), model.calls
    # the session is older than thread_close (7 days): its new messages, then its one final pass
    assert (summary["source"], summary["threads_seen"], summary["segments_processed"]) == ("claude_code", 1, 2)
    again = runner.invoke(cli.app, ["ingest", "--source", "claude_code"])
    assert again.exit_code == 0, again.output
    summary = json.loads(again.stdout)
    assert (summary["segments_processed"], summary["created"], model.calls) == (0, 0, calls)
