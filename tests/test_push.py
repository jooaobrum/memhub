"""Push ingestion: `Memory.add(messages, infer=True)` runs the pipeline on the messages passed in, at once.

Runs on the in-memory backend with scripted models - no Docker. `msg_ids` computes the ids the way the app's
messages get them, so the scripted extractor can cite them as evidence."""
from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memhub import AddResult, Dropped, Memory
from memhub.config import TypeConfig
from memhub.pipeline.extract import Candidate, Evidence
from memhub.pipeline.push import message_id, to_interactions, to_messages
from memhub.pipeline.reconcile import Verdict
from memhub.service import ValidationError
from memhub.storage import Capability
from memhub.storage.rules import Conflict
from tests.fakes import PARSE_ERROR, FakeChatModel, FakeEmbeddings, extraction

ROOT = Path(__file__).resolve().parents[1]
MEM = "memory" + "://"
IDS = dict(workspace_id="acme", user_id="anna", run_id="run-1")
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def at(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def conv(*turns: str, start: int = 0) -> list[dict]:
    """user, assistant, user, ... with one minute between messages."""
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": t, "timestamp": at(start + i)} for i, t in enumerate(turns)]


def msg_ids(messages: list[dict], *, workspace_id="acme", user_id="anna", thread_id="run-1") -> list[str]:
    return [m.message_id for m in to_interactions(to_messages(messages), workspace_id=workspace_id, user_id=user_id,
                                                  thread_id=thread_id, trace_id="t", now=T0)]


def cand(content: str, quote: str, mid: str, *, type="fact", scope="user", utility=5, **fields) -> Candidate:
    return Candidate(type=type, scope=scope, fields={"content": content, **fields}, utility=utility, applies_generally=True,
                     assertion="stated", evidence=[Evidence(message_id=mid, quote=quote, claim_source="user")])


def _memory(settings, llm=None, embedder=None, db=MEM) -> Memory:
    m = Memory(db=db, llm=llm, embedder=embedder or FakeEmbeddings(16), config=settings)
    m.init()
    return m


def _rows(m: Memory) -> list[dict]:
    with m.store.unit_of_work() as uow:
        return uow.memories.list_memories()


def _runs(m: Memory) -> list[dict]:
    with m.store.unit_of_work() as uow:
        return sorted((r for s in uow.runs.list_run_summaries(last=100) for r in uow.runs.run_detail(s["run_id"])),
                      key=lambda r: (r["processed_at"], r["last_message_at"]))


# --- S1: messages in, memories out ------------------------------------------------------------------------------


def test_push_returns_events_and_drops(settings):
    messages = conv("I live in Lyon", "Noted", "Our team deploys on Fridays", "Ok", "I have a cat called Tom", "Nice")
    ids = msg_ids(messages)
    llm = FakeChatModel([extraction(
        cand("User lives in Lyon", "I live in Lyon", ids[0]),
        cand("The team deploys on Fridays", "Our team deploys on Fridays", ids[2], scope="workspace"),
        cand("User has a dog", "I have a dog", ids[4]),  # the quote is not in the message
    )], unrelated_by_default=True)
    m = _memory(settings, llm)
    out = m.add(messages, infer=True, **IDS)
    assert isinstance(out, AddResult) and out.run_id == "run-1"
    assert [(e.event, e.status, e.scope, e.content) for e in out.results] == [
        ("ADD", "active", "user", "User lives in Lyon"),
        ("PENDING", "candidate", "workspace", "The team deploys on Fridays"),
    ]
    assert out.dropped == [Dropped(content="User has a dog", reason="ungrounded")]
    rows = {r["content"]: r for r in _rows(m)}
    assert [e.id for e in out.results] == [str(rows[e.content]["memory_id"]) for e in out.results]

    nothing = FakeChatModel([extraction()], unrelated_by_default=True)
    quiet = _memory(settings, nothing)
    assert quiet.add(conv("hello", "hi"), infer=True, **IDS) == AddResult(run_id="run-1", results=[], dropped=[])


def test_events_follow_the_reconcile_decision(settings):
    first = conv("I live in Lyon", "Noted", "I prefer answers in English", "Ok")
    ids = msg_ids(first)
    llm = FakeChatModel([extraction(
        cand("User lives in Lyon", "I live in Lyon", ids[0]),
        cand("Prefers answers in English", "I prefer answers in English", ids[2], type="preference", key="language"),
    )], unrelated_by_default=True)
    m = _memory(settings, llm)
    stored = {e.content: e for e in m.add(first, infer=True, **IDS).results}

    again = first + conv("I live in Lyon, as I said", "Sure", "Actually I prefer answers in Portuguese", "Ok", start=10)
    later = msg_ids(again)
    llm.queue += [
        extraction(
            cand("User lives in Lyon", "I live in Lyon", later[4]),
            cand("Prefers answers in Portuguese", "I prefer answers in Portuguese", later[6], type="preference", key="language"),
        ),
        Verdict(verdict="updates"),  # the judge on the language slot: a newer value
    ]
    events = {e.content: e for e in m.add(again, infer=True, **IDS).results}
    merged, updated = events["User lives in Lyon"], events["Prefers answers in Portuguese"]
    assert (merged.event, merged.id, merged.version) == ("MERGE", stored["User lives in Lyon"].id, 1)
    assert (updated.event, updated.id, updated.version, updated.status) == (
        "UPDATE", stored["Prefers answers in English"].id, 2, "active")

    # a person's own (verified) value is never overwritten by what the extractor heard
    bob = dict(IDS, user_id="bob", run_id="run-b")
    mine = m.add({"content": "Prefers short answers", "key": "answer_style"}, type="preference", **bob).results[0]
    said = [{"role": "user", "content": "Please give me long answers"}, {"role": "assistant", "content": "Ok"}]  # dated now: after the add
    llm.queue += [extraction(cand("Prefers long answers", "give me long answers", msg_ids(said, user_id="bob", thread_id="run-b")[0],
                                  type="preference", key="answer_style"))]
    [conflict] = m.add(said, infer=True, **bob).results
    assert (conflict.event, conflict.status, conflict.conflicts_with) == ("CONFLICT", "candidate", mine.id)


def test_rows_carry_the_call_ids_and_push_evidence(settings):
    messages = conv("I live in Lyon", "Noted")
    ids = msg_ids(messages, thread_id="chat-7")
    llm = FakeChatModel([extraction(cand("User lives in Lyon", "I live in Lyon", ids[0]))], unrelated_by_default=True)
    m = _memory(settings, llm)
    out = m.add(messages, infer=True, thread_id="chat-7", agent_id="bot", **IDS)
    [row] = _rows(m)
    assert (row["run_id"], row["agent_id"], row["created_by"]) == ("run-1", "bot", "extractor")
    assert [(e["source"], e["thread_id"], e["message_id"]) for e in row["evidence"]] == [("push", "chat-7", ids[0])]
    assert "embedding" not in json.dumps(out.model_dump())

    llm.queue += [extraction(cand("User works as a welder", "I work as a welder", msg_ids(conv("I work as a welder", "Ok"))[0]))]
    m.add(conv("I work as a welder", "Ok"), infer=True, **IDS)  # no agent_id
    welder = next(r for r in _rows(m) if r["content"] == "User works as a welder")
    assert (welder["run_id"], welder["agent_id"]) == ("run-1", None)


def test_segments_by_user_turns_without_idle_wait(settings):
    settings.ingestion.segment_max_user_turns = 2
    now = datetime.now(timezone.utc)
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}", "timestamp": (now - timedelta(seconds=1)).isoformat()}
                for i in range(10)]  # 5 user turns, last said one second ago
    llm = FakeChatModel([extraction(), extraction(), extraction()], unrelated_by_default=True)
    m = _memory(settings, llm)
    m.add(messages, infer=True, **IDS)
    runs = _runs(m)
    ids = msg_ids(messages)
    assert [(r["first_message_id"], r["last_message_id"]) for r in runs] == [(ids[0], ids[3]), (ids[4], ids[7]), (ids[8], ids[9])]
    assert all(r["source"] == "push" and not r["final_pass"] for r in runs) and llm.queue == []


def test_remember_command_in_pushed_messages(settings):
    settings.admission.threshold = 0.99  # background extraction alone would keep nothing
    messages = conv("/remember I am allergic to peanuts", "Noted")
    ids = msg_ids(messages)
    told = extraction(cand("Is allergic to peanuts", "I am allergic to peanuts", ids[0], utility=1))
    llm = FakeChatModel([told], unrelated_by_default=True)  # the command message is all there is: no background call
    m = _memory(settings, llm)
    [event] = m.add(messages, infer=True, **IDS).results
    [row] = _rows(m)
    assert (event.event, row["content"], row["created_by"]) == ("ADD", "Is allergic to peanuts", "remember")


def test_infer_defaults_to_false(settings):
    llm = FakeChatModel([], unrelated_by_default=True)
    m = _memory(settings, llm)
    a = m.add("Prefers tea", **IDS).results[0]
    b = m.add({"content": "Lives in Lyon"}, type="fact", **IDS).results[0]
    assert (a.event, b.event, llm.calls) == ("ADD", "ADD", 0)
    assert sorted(r["content"] for r in _rows(m)) == ["Lives in Lyon", "Prefers tea"]


# --- S2: the same messages twice --------------------------------------------------------------------------------


def test_same_messages_twice_cost_nothing(settings):
    messages = conv("I live in Lyon", "Noted", "I work as a welder", "Ok")
    ids = msg_ids(messages)
    llm = FakeChatModel([extraction(cand("User lives in Lyon", "I live in Lyon", ids[0]))], unrelated_by_default=True)
    m = _memory(settings, llm)
    m.add(messages, infer=True, **IDS)
    calls, rows, runs = llm.calls, len(_rows(m)), len(_runs(m))
    again = m.add(messages, infer=True, **IDS)
    assert (again.results, again.dropped) == ([], [])
    assert (llm.calls, len(_rows(m)), len(_runs(m))) == (calls, rows, runs)


def test_only_messages_after_the_watermark(settings):
    six = conv("m1", "m2", "m3", "m4", "m5", "m6")
    ids = msg_ids(six)
    cases = {
        "superset": (six[:4], six, ("m5", "m6")),
        "window": (six[:4], six[2:], ("m5", "m6")),
        "new turns only": (six[:2], six[2:4], ("m3", "m4")),
    }
    # a window that repeats an answer, no timestamps: the new turn after it is still extracted
    first = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Noted"},
             {"role": "user", "content": "I live in Lyon"}, {"role": "assistant", "content": "Noted"}]
    window = first[2:] + [{"role": "user", "content": "I work as a welder"}, {"role": "assistant", "content": "Noted"}]
    cases["window with a repeated answer"] = (first, window, ("I work as a welder", "Noted"))
    for name, (before, after, expected) in cases.items():
        seen = []

        class Spy(FakeChatModel):
            def with_structured_output(self, schema, **kw):
                runnable = super().with_structured_output(schema, **kw)
                invoke = runnable.invoke
                runnable.invoke = lambda messages, **k: (seen.append(str(messages)), invoke(messages, **k))[1]
                return runnable

        llm = Spy([extraction(), extraction()], unrelated_by_default=True)
        m = _memory(settings, llm)
        m.add(before, infer=True, **IDS)
        seen.clear()
        m.add(after, infer=True, **IDS)
        last = _runs(m)[-1]
        after_ids = msg_ids(after)  # the ids this call gave its messages
        first_new = len(after) - 2  # in every case the two new messages close the call
        assert tuple(m_["content"] for m_ in after[first_new:]) == expected, name
        assert (last["first_message_id"], last["last_message_id"]) == (after_ids[first_new], after_ids[-1]), name
        new = {m_["content"] for m_ in after[first_new:]}
        assert seen and all(t in seen[0] for t in new), name


def test_message_ids():
    def formula(ws, user, thread, role, content, stamp, prev_role, prev_content, n):
        parts = [ws, user, thread, role, content, stamp, prev_role, prev_content, str(n)]
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]

    assert message_id("acme", "anna", "t", "user", "yes", "", "", "", 0) == formula("acme", "anna", "t", "user", "yes", "", "", "", 0)
    two = msg_ids([{"role": "user", "content": "yes"}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "yes"}])
    assert two[0] != two[2]
    assert two[0] == formula("acme", "anna", "run-1", "user", "yes", "", "", "", 0)
    assert two[2] == formula("acme", "anna", "run-1", "user", "yes", "", "assistant", "ok", 0)
    loop = msg_ids([{"role": "user", "content": "yes"}, {"role": "assistant", "content": "ok"}] * 2)
    assert len(set(loop)) == 4  # the same pair twice: four messages
    one = [{"role": "user", "content": "yes"}]
    assert msg_ids(one) == msg_ids(one)
    assert msg_ids(one) != msg_ids(one, user_id="bob")
    assert msg_ids(one) != msg_ids(one, thread_id="other")
    assert msg_ids(one) != msg_ids([{"role": "user", "content": "yes", "timestamp": at(0)}])

def test_thread_defaults_to_the_run(settings):
    m = _memory(settings, FakeChatModel([extraction()], unrelated_by_default=True))
    one = [{"role": "user", "content": "yes"}]
    m.add("yes", infer=True, **IDS)  # no thread_id: the run is the thread
    with m.store.unit_of_work() as uow:
        assert uow.runs.get_watermark(source="push", thread_id="run-1")["last_message_id"] == msg_ids(one)[0]


# --- S3: calls that cannot run ----------------------------------------------------------------------------------


def test_invalid_calls_raise_before_any_work(settings):
    settings.llm = None  # no chat model passed, none configured
    no_llm = _memory(settings, llm=None)
    with pytest.raises(ValidationError, match="llm"):
        no_llm.add(conv("I live in Lyon", "ok"), infer=True, **IDS)
    for field in ("workspace_id", "user_id", "run_id"):
        llm, emb = FakeChatModel([extraction()]), FakeEmbeddings(16)
        m = _memory(settings, llm, emb)
        emb.calls = 0
        ids = {k: v for k, v in IDS.items() if k != field}
        with pytest.raises(ValidationError, match=field):
            m.add(conv("I live in Lyon", "ok"), infer=True, **ids)
        assert (llm.calls, emb.calls, _rows(m), _runs(m)) == (0, 0, [], [])


def test_message_validation(settings):
    llm = FakeChatModel([], unrelated_by_default=True)
    m = _memory(settings, llm)
    bad = [[], [{"role": "user"}], [{"role": "user", "content": 3}], [{"role": "assistant", "content": "hi"}], {"content": "x"}, ["hi"]]
    for data in bad:
        with pytest.raises(ValidationError):
            m.add(data, infer=True, **IDS)
    with pytest.raises(ValidationError, match="type"):
        m.add("I live in Lyon", infer=True, type="fact", **IDS)
    with pytest.raises(ValidationError, match="infer=True"):
        m.add(conv("I live in Lyon", "ok"), **IDS)
    assert llm.calls == 0 and _rows(m) == []

    seen = []

    class Spy(FakeChatModel):
        def with_structured_output(self, schema, **kw):
            runnable = super().with_structured_output(schema, **kw)
            invoke = runnable.invoke
            runnable.invoke = lambda messages, **k: (seen.append(str(messages)), invoke(messages, **k))[1]
            return runnable

    spy = _memory(settings, Spy([extraction()], unrelated_by_default=True))
    spy.add([{"role": "system", "content": "You are a bot with a secret"}, {"role": "user", "content": "hi there"}], infer=True, **IDS)
    assert "hi there" in seen[0] and "secret" not in seen[0]


def test_failed_segment_raises_and_is_retried(settings):
    settings.ingestion.segment_max_user_turns = 1
    messages = conv("I live in Lyon", "Noted", "I work as a welder", "Ok")
    ids = msg_ids(messages)

    class FailsOnce(FakeEmbeddings):
        def __init__(self):
            super().__init__(16)
            self.fail_on = "User works as a welder"

        def embed_query(self, text):
            if text == self.fail_on:
                self.fail_on = None
                raise ConnectionError("embedding provider down")
            return super().embed_query(text)

    llm = FakeChatModel([extraction(cand("User lives in Lyon", "I live in Lyon", ids[0])),
                         extraction(cand("User works as a welder", "I work as a welder", ids[2]))], unrelated_by_default=True)
    m = _memory(settings, llm, FailsOnce())
    with pytest.raises(ConnectionError, match="provider down"):
        m.add(messages, infer=True, **IDS)
    assert [r["content"] for r in _rows(m)] == ["User lives in Lyon"]
    assert [r["last_message_id"] for r in _runs(m)] == [ids[1]]

    llm.queue += [extraction(cand("User works as a welder", "I work as a welder", ids[2]))]
    out = m.add(messages, infer=True, **IDS)
    assert [e.content for e in out.results] == ["User works as a welder"] and llm.queue == []
    assert [r["last_message_id"] for r in _runs(m)] == [ids[1], ids[3]]


class _Locks:
    """The backend of a `Memory`, with its thread lock either held by someone else or counted."""

    def __init__(self, inner, *, held=False, capable=True):
        self.inner, self.held, self.asked = inner, held, []
        self.name = inner.name
        self.capabilities = inner.capabilities if capable else inner.capabilities - {Capability.PARALLEL_INGEST}

    def unit_of_work(self):
        return self.inner.unit_of_work()

    def init(self, **kw):
        return self.inner.init(**kw)

    @contextmanager
    def thread_lock(self, source, thread_id):
        self.asked.append((source, thread_id))
        yield not self.held


def test_thread_lock(settings):
    llm = FakeChatModel([extraction()], unrelated_by_default=True)
    m = _memory(settings, llm)
    busy = _Locks(m.store, held=True)
    m.store = m.service.store = busy
    with pytest.raises(Conflict, match="run-1"):
        m.add(conv("I live in Lyon", "ok"), infer=True, **IDS)
    assert busy.asked == [("push", "run-1")] and llm.calls == 0 and _runs(m) == []

    single = _Locks(busy.inner, capable=False)
    m.store = m.service.store = single
    m.add(conv("I live in Lyon", "ok"), infer=True, **IDS)
    assert single.asked == [] and llm.calls == 1 and len(_runs(m)) == 1

    # two pushes to one thread at once on the in-memory backend: one runs, the other is told
    real = _memory(settings, FakeChatModel([extraction(), extraction()], unrelated_by_default=True))
    gate, results = threading.Event(), []
    inner_lock = real.store.thread_lock

    @contextmanager
    def slow_lock(source, thread_id):
        with inner_lock(source, thread_id) as got:
            gate.set() if got else None
            if got:
                threading.Event().wait(0.3)
            yield got

    real.store.thread_lock = slow_lock

    def push():
        try:
            real.add(conv("I live in Lyon", "ok"), infer=True, **IDS)
            results.append("ran")
        except Conflict:
            results.append("conflict")

    first = threading.Thread(target=push)
    first.start()
    gate.wait()
    push()
    first.join()
    assert sorted(results) == ["conflict", "ran"]


def test_extract_error_is_a_drop(settings):
    llm = FakeChatModel([PARSE_ERROR] * 2, unrelated_by_default=True)  # the first answer and the one retry
    m = _memory(settings, llm)
    out = m.add(conv("I live in Lyon", "ok"), infer=True, **IDS)
    assert (out.results, out.dropped) == ([], [Dropped(content=None, reason="extract_error")])
    assert [r["status"] for r in _runs(m)] == ["extract_error"]


# --- docs -------------------------------------------------------------------------------------------------------


def test_push_docs():
    for doc in ("README.md", "docs/integration.md"):
        text = (ROOT / doc).read_text()
        assert "infer=True" in text and "AddResult" in text, doc
        assert "infer` defaults to `False`" in text or "infer=False` is the default" in text, doc


def test_a_new_version_waiting_for_review_is_pending(settings):
    settings.types["preference"].scopes = ["workspace"]  # a reviewed, keyed workspace type
    m = _memory(settings, FakeChatModel([], unrelated_by_default=True))
    with m.store.unit_of_work() as uow:  # an active, unverified workspace value, as an older config may have left it
        old = uow.memories.add_memory(
            type="preference", schema_version=1, scope="workspace", workspace_id="acme", user_id=None,
            content="Answers in English", payload={"content": "Answers in English", "key": "language"}, entities=[],
            embedding=FakeEmbeddings(16).embed_query("Answers in English"), evidence=[], status="active", verified=False,
            created_by="extractor",
        )
    said = [{"role": "user", "content": "From now on the team answers in Portuguese"}, {"role": "assistant", "content": "Ok"}]
    m.llm.queue += [extraction(cand("Answers in Portuguese", "the team answers in Portuguese", msg_ids(said)[0],
                                    type="preference", scope="workspace", key="language")), Verdict(verdict="updates")]
    [event] = m.add(said, infer=True, **IDS).results
    assert (event.event, event.status, event.version, event.id) == ("PENDING", "candidate", 2, str(old["memory_id"]))
