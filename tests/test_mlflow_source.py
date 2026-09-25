import pytest

pytest.importorskip("mlflow")

from memhub.config import SourceConfig
from memhub.sources.mlflow import MLflowSource
from tests.mlflow_sim import write_traces


@pytest.fixture
def uri(tmp_path):
    return f"sqlite:///{tmp_path}/mlflow.db"


def source(uri, **fields):
    cfg = SourceConfig(kind="mlflow", tracking_uri=uri, experiment="chat", fields=fields)
    return MLflowSource(cfg, workspace_id="ws")


def turn(q, a, session="s1", user="u1", **extra):
    return {"request": {"query": q}, "response": {"answer": a}, "session": session, "user": user, **extra}


FIELDS = {"user_content": "query", "assistant_content": "answer"}


def test_one_turn_becomes_user_and_assistant_interactions(uri):
    (tid,) = write_traces(uri, "chat", [turn("I prefer tabs", "Noted", user="alice")])
    src = source(uri, **FIELDS)
    user, assistant = list(src.read())
    assert (user.role, user.content, user.message_id) == ("user", "I prefer tabs", tid)
    assert (assistant.role, assistant.content, assistant.message_id) == ("assistant", "Noted", f"{tid}:a")
    for m in (user, assistant):
        assert (m.thread_id, m.user_id, m.workspace_id, m.trace_id) == ("s1", "alice", "ws", tid)
    assert user.timestamp == assistant.timestamp and user.timestamp.tzinfo is not None
    assert src.skipped == 0


def test_multi_turn_thread_in_order(uri):
    write_traces(uri, "chat", [turn("one", "A1"), turn("two", "A2"), turn("other", "A3", session="s2")])
    out = list(source(uri, **FIELDS).read())
    assert [m.content for m in out] == ["one", "A1", "two", "A2", "other", "A3"]
    assert [m.thread_id for m in out] == ["s1"] * 4 + ["s2"] * 2
    assert out[0].timestamp <= out[2].timestamp


def test_trace_without_session_or_response_is_skipped(uri):
    write_traces(uri, "chat", [turn("x", "y", session=None), turn("q", "", session="s1"), turn("ok", "fine")])
    src = source(uri, **FIELDS)
    assert [m.content for m in src.read()] == ["ok", "fine"]
    assert src.skipped == 2


def test_plain_string_request_and_response_by_default(uri):
    write_traces(uri, "chat", [{"request": "hello", "response": "hi there", "session": "s", "user": "u"}])
    assert [m.content for m in source(uri).read()] == ["hello", "hi there"]


def test_dotted_path_with_list_index(uri):
    write_traces(uri, "chat", [{
        "request": {"messages": [{"content": "first"}, {"content": "latest"}]},
        "response": {"choices": [{"message": {"content": "reply"}}]},
        "session": "s", "user": "u",
    }])
    src = source(uri, user_content="messages.-1.content", assistant_content="choices.0.message.content")
    assert [m.content for m in src.read()] == ["latest", "reply"]


def test_custom_field_mapping_and_metadata_from_tags(uri):
    write_traces(uri, "chat", [turn("q", "a", session=None, user=None,
                                    tags={"conv": "c9", "who": "bob", "channel": "web"})])
    src = source(uri, thread_id="conv", user_id="who", metadata=["channel", "missing"], **FIELDS)
    user, _ = list(src.read())
    assert (user.thread_id, user.user_id, user.metadata) == ("c9", "bob", {"channel": "web"})


def test_errored_trace_without_response_is_skipped(uri):
    write_traces(uri, "chat", [turn("q", "a"), turn("boom", "never", error=True)])
    src = source(uri, **FIELDS)
    assert [m.content for m in src.read()] == ["q", "a"]
    assert src.skipped == 1


def test_unknown_experiment_raises(uri):
    write_traces(uri, "other", [turn("q", "a")])
    with pytest.raises(ValueError):
        list(source(uri, **FIELDS).read())
