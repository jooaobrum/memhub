from __future__ import annotations

import json
from datetime import timezone

from memhub.config import SignalsConfig, SourceConfig
from memhub.sources.jsonl import JSONLSource

FIELDS = {
    "thread_id": "chat_id", "user_id": "chat_id", "message_id": "message_id", "timestamp": "timestamp",
    "trace_id": "trace_id", "user_content": "user_query", "assistant_content": "answer",
    "metadata": ["intent", "error"],
}


def _cfg(signals=None) -> SourceConfig:
    return SourceConfig(kind="jsonl", path="log.jsonl", one_line_per="turn", fields=FIELDS, signals=signals)


def _write(path, lines):
    path.write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n")


def test_turn_emits_user_then_assistant(tmp_path):
    _write(tmp_path / "log.jsonl", [{
        "chat_id": 42, "message_id": "m1", "timestamp": "2026-01-01T10:00:00", "trace_id": "t1",
        "user_query": "hi", "answer": "hello", "intent": "greeting", "ignored": 1,
    }])
    src = JSONLSource(_cfg(), workspace_id="ws", base_dir=tmp_path)
    user, assistant = list(src.read())
    assert (user.role, user.message_id, user.content) == ("user", "m1", "hi")
    assert (assistant.role, assistant.message_id, assistant.content) == ("assistant", "m1:a", "hello")
    assert user.thread_id == user.user_id == "42"
    assert user.workspace_id == "ws" and user.trace_id == "t1"
    assert user.timestamp == assistant.timestamp and user.timestamp.tzinfo == timezone.utc
    assert user.metadata == assistant.metadata == {"intent": "greeting"}


def test_malformed_and_incomplete_lines_are_skipped_and_counted(tmp_path):
    good = {"chat_id": "c", "message_id": "m1", "timestamp": "2026-01-01T10:00:00Z", "user_query": "q"}
    _write(tmp_path / "log.jsonl", [
        good, "{not json", {**good, "chat_id": None}, {k: v for k, v in good.items() if k != "message_id"},
        {**good, "timestamp": "yesterday"}, "[1, 2]",
    ])
    src = JSONLSource(_cfg(), workspace_id="ws", base_dir=tmp_path)
    assert [m.message_id for m in src.read()] == ["m1"]
    assert src.skipped == 5


def test_signals_are_mapped_through_config(tmp_path):
    _write(tmp_path / "log.jsonl", [])
    _write(tmp_path / "fb.jsonl", [
        {"chat_id": "c", "message_id": "m1", "rating": "down"},
        {"chat_id": "c", "message_id": "m2", "rating": "up"},
        {"chat_id": "c", "message_id": "m3", "rating": "meh"},
        "garbage",
    ])
    cfg = _cfg(SignalsConfig(
        path="fb.jsonl", join_on={"message_id": "message_id", "thread_id": "chat_id"},
        map={"rating": {"down": "feedback_down", "up": "feedback_up"}},
    ))
    got = JSONLSource(cfg, workspace_id="ws", base_dir=tmp_path).signals()
    assert [(s["thread_id"], s["message_id"], s["kind"]) for s in got] == [
        ("c", "m1", "feedback_down"), ("c", "m2", "feedback_up"),
    ]


def test_signals_without_a_file_are_empty(tmp_path):
    assert JSONLSource(_cfg(), workspace_id="ws", base_dir=tmp_path).signals() == []
    cfg = _cfg(SignalsConfig(path="missing.jsonl"))
    assert JSONLSource(cfg, workspace_id="ws", base_dir=tmp_path).signals() == []
