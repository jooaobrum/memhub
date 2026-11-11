"""PR 5 demo: a script pushes a 6-turn conversation; the user's memories are active and found at once, the
workspace one waits in the queue until a reviewer approves it, then every user of the workspace finds it.
On the in-memory backend, SQLite (no Docker) and Postgres (the Docker fixture)."""
from __future__ import annotations

import uuid

import pytest

from memhub import Memory
from memhub.pipeline.extract import Candidate, Evidence
from memhub.pipeline.push import to_interactions, to_messages
from tests.fakes import FakeChatModel, FakeEmbeddings, extraction

ANNA = dict(workspace_id="plant-7", user_id="anna", run_id="trace-42")
BOB = dict(workspace_id="plant-7", user_id="bob", run_id="trace-43")
LEAD = dict(workspace_id="plant-7", user_id="lead", run_id="trace-44", roles=["workspace_admin"])

CONVERSATION = [
    {"role": "user", "content": "I work the night shift on line 2"},
    {"role": "assistant", "content": "Noted, night shift on line 2."},
    {"role": "user", "content": "I prefer short answers with the steps numbered"},
    {"role": "assistant", "content": "Understood."},
    {"role": "user", "content": "Pump P4 cavitates when the suction valve is less than half open"},
    {"role": "assistant", "content": "Thanks, open the suction valve fully before starting P4."},
]


def _db(kind: str, request, tmp_path):
    if kind == "memory":
        return "memory" + "://"
    if kind == "sqlite":
        return f"sqlite:///{tmp_path / 'push.db'}"
    return request.getfixturevalue("pg_dsn")


@pytest.mark.parametrize("kind", ["memory", "sqlite", "postgres"])
def test_push_a_conversation_then_review(kind, request, tmp_path):
    ids = [m.message_id for m in to_interactions(to_messages(CONVERSATION), workspace_id="plant-7", user_id="anna",
                                                 thread_id="trace-42", trace_id="trace-42", now=None)]

    def cand(content, quote, mid, scope="user"):
        return Candidate(type="fact", scope=scope, fields={"content": content}, utility=5, applies_generally=True,
                         assertion="stated", evidence=[Evidence(message_id=mid, quote=quote, claim_source="user")])

    llm = FakeChatModel([extraction(
        cand("Works the night shift on line 2", "I work the night shift on line 2", ids[0]),
        cand("Prefers short answers with numbered steps", "I prefer short answers with the steps numbered", ids[2]),
        cand("Pump P4 cavitates when the suction valve is less than half open",
             "Pump P4 cavitates when the suction valve is less than half open", ids[4], scope="workspace"),
    ), extraction()], unrelated_by_default=True)  # the second answer: the default config's glossary-term call
    m = Memory(db=_db(kind, request, tmp_path), llm=llm, embedder=FakeEmbeddings(16),
               config={"project_prefix": f"p{uuid.uuid4().hex[:8]}", "scopes": ["user", "workspace"]})
    m.init()

    out = m.add(CONVERSATION, infer=True, **ANNA)
    events = {e.content: e for e in out.results}
    assert [(e.event, e.status, e.scope) for e in out.results] == [
        ("ADD", "active", "user"), ("ADD", "active", "user"), ("PENDING", "candidate", "workspace")]
    pump = events["Pump P4 cavitates when the suction valve is less than half open"]

    found = [r["content"] for r in m.search("night shift line 2", **ANNA)]
    assert found[0] == "Works the night shift on line 2"
    assert pump.content not in [r["content"] for r in m.search("pump P4 suction valve", **ANNA)]
    assert [str(r["memory_id"]) for r in m.queue(**LEAD)] == [pump.id]
    assert pump.content not in [r["content"] for r in m.search("pump P4 suction valve", **BOB)]
    assert "Works the night shift on line 2" not in [r["content"] for r in m.search("night shift line 2", **BOB)]

    approved = m.approve(pump.id, **LEAD)
    assert (approved.id, approved.status) == (pump.id, "active")
    assert m.search("pump P4 suction valve", **BOB)[0]["content"] == pump.content
    assert m.queue(**LEAD) == []
