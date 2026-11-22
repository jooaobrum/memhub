"""Domain 3, outpatient clinic: clinicians use an assistant that remembers patients' conditions, allergies and
medication, and how each clinician likes to work. Nothing is used before a doctor reviews it.

The project: `review: all`, a `patient` subject that is isolated and guarded (`require_filter`: never returned by a
search that does not name it), a `diagnosis` that always needs a doctor, a clinician `preference` that does not,
medication that expires after 14 days.

    C1  review: all: everything extracted waits, a nurse cannot approve her own, a doctor approves
    C2  the patient guard: unfiltered search, toolkit, CLI and middleware never show a patient's memory
    C3  two patients with the same words stay apart; a widened search adds general knowledge, never another patient
    C4  a doctor corrects an allergy while approving it; history keeps the nurse's proposal
    C5  a LangChain agent with `MemoryMiddleware`: the clinician's preferences in its prompt, patients not, a proposal
    C6  medication expires: stale rows leave search; `valid_until` filters; the list of stale rows
    C7  archive, history, and the hard delete rules: rejected and own rows only
    C8  a deepagents agent writes the clinician's own page: a diagnosis waits "(needs review)", an unsaid line is a flag
    C9  pages from Python and the CLI: README with counts, an area, a subject, an area that does not exist
    C10 the ledger's schema version: refused when old, `memhub migrate`, then used (Postgres: a v0.1.0 ledger)
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from memhub import Memory, NotFound, PermissionDenied, SchemaOutdated, ValidationError
from tests.e2e.domains.harness import (NOW, TODAY, Agent, append, call, cand, deep_turn, deepagents_backend,
                                       memory_section, project)
from tests.fakes import FakeEmbeddings

CLINIC = """
---
review: all
types:
  fact: {builtin: true, area: optional}
  diagnosis: {template: "{condition}[ since {since?}]", review: true, area: optional}
  allergy: {template: "Allergic to {substance}[ ({reaction?})]", area: optional}
  medication: {template: "Takes {drug} {dose}", ttl: 14d, area: optional}
  preference: {builtin: true, review: false, retrieval: always}
areas:
  conditions: chronic and acute conditions
  medication: drugs, doses, allergies
  care: how the care team works
entities:
  patient: {isolate: true, require_filter: true}
  ward: tag
---
# What to remember

Patients' conditions, allergies and medication as the clinician states them, and how each clinician works.

- Bind every clinical memory to its patient. Never infer a diagnosis the clinician did not state.
"""

WS = "clinic-north"
DOC = ["workspace_admin"]
P1 = {"type": "patient", "id": "MRN-0001"}
P2 = {"type": "patient", "id": "MRN-0002"}


@pytest.fixture(params=["sqlite", "postgres"])
def p(request, tmp_path, monkeypatch):
    return project(request, tmp_path, monkeypatch, name="clinic", memory_md=CLINIC, workspace=WS,
                   yaml_extra={"scopes": ["user", "workspace"]})


def _patient_note(p, user="ines", patient=P1, text="Has type 2 diabetes since 2019", thread=None):
    return p.push(user, [f"Patient note: {text}", "Recorded."],
                  cand("diagnosis", {"condition": "type 2 diabetes", "since": "2019"}, text, area="conditions"),
                  thread=thread or uuid.uuid4().hex, entities=[patient]).results


def test_c1_review_all(p):
    out = p.push("ines", ["Patient note: Has type 2 diabetes since 2019", "Recorded.",
                          "I like the lab values listed first", "OK.",
                          "The night shift hands over at 7am", "OK."],
                 cand("diagnosis", {"condition": "type 2 diabetes", "since": "2019"}, "Has type 2 diabetes since 2019",
                      entities=[P1]),
                 cand("preference", {"key": "layout", "content": "Wants lab values listed first"},
                      "I like the lab values listed first"),
                 cand("fact", "The night shift hands over at 7am", "The night shift hands over at 7am"))
    p.model.done()
    by = {e.type: e for e in out.results}
    assert {t: (e.event, e.status) for t, e in by.items()} == {
        "diagnosis": ("PENDING", "candidate"), "preference": ("ADD", "active"), "fact": ("PENDING", "candidate")}
    ines = p.as_("ines")
    for kind in ("diagnosis", "fact"):
        with pytest.raises(PermissionDenied, match=f"a {kind} needs review by an approving role, not its owner"):
            ines.approve(by[kind].id)
    assert {r["type"] for r in ines.queue()} == {"diagnosis", "fact"}
    assert p.as_("raul").queue() == []  # another nurse sees none of her personal candidates
    assert {r["type"] for r in p.admin("dr-silva").queue()} == {"diagnosis", "fact"}
    done = p.admin("dr-silva").approve(by["diagnosis"].id, note="confirmed HbA1c")
    assert (done.event, done.status) == ("ADD", "active")
    assert p.as_("ines").get(by["diagnosis"].id)["reviewed_by"] == "dr-silva"
    config = str(p.cli("config", "show"))
    assert "review.diagnosis" in config and "review.preference" in config


def test_c2_patient_guard(p):
    [dx] = _patient_note(p)
    p.admin("dr-silva").approve(dx.id)
    ines = p.as_("ines")
    ines.add("Diabetes education leaflets are in room 4")
    assert p.contents(ines.search("diabetes", top_k=10)) == ["Diabetes education leaflets are in room 4"]
    assert p.contents(ines.search("diabetes", filters={"entities": [P1]})) == ["type 2 diabetes since 2019"]
    assert p.contents(ines.search("diabetes", filters={"NOT": {"entities": [P2]}}, top_k=10)) == [
        "Diabetes education leaflets are in room 4"]  # a NOT does not name the patient
    assert p.contents(ines.get_all(filters={"entities": [P1]})) == ["type 2 diabetes since 2019"]
    kit = p.memory.tools(**p.ids("ines"))
    assert "type 2 diabetes" not in kit.call("search_memory", {"query": "diabetes"})
    assert "type 2 diabetes" in kit.call("search_memory", {"query": "diabetes", "filters": {"entities": [P1]}})
    assert "type 2 diabetes" not in str(p.cli("search", "diabetes", "--ws", WS, "--user", "ines"))
    assert "type 2 diabetes" in str(p.cli("search", "diabetes", "--ws", WS, "--user", "ines", "--entity",
                                          "patient:MRN-0001"))
    mw = p.memory.middleware(workspace_id=WS, user_id="ines")
    state = mw.before_agent({"messages": [HumanMessage("what about diabetes?")]}, type("R", (), {"context": None}))
    assert "Diabetes education leaflets" in state["memory_turn"] and "type 2" not in state["memory_turn"]


def test_c3_patients_stay_apart(p):
    [a] = _patient_note(p, patient=P1)
    [b] = _patient_note(p, patient=P2)
    [c] = _patient_note(p, patient=P1)
    p.model.done()
    assert a.id != b.id and (c.event, c.id) == ("MERGE", a.id)
    doc = p.admin("dr-silva")
    for e in (a, b):
        doc.approve(e.id)
    ines = p.as_("ines")
    general = ines.add("Type 2 diabetes reviews are every 3 months").results[0]
    hits = ines.search("type 2 diabetes", filters={"entities": [P1]}, widen=True, top_k=10)
    assert [(str(h["memory_id"]), h["matched_on"]) for h in hits] == [(a.id, "patient:MRN-0001"),
                                                                      (general.id, "general")]
    wide = ines.search("type 2 diabetes", filters={"entity_type": "patient"}, top_k=10)
    assert {str(h["memory_id"]) for h in wide} == {a.id, b.id}  # naming the type on purpose: every patient


def test_c4_allergy_corrected_on_review(p):
    [al] = p.push("ines", ["Patient says she is allergic to penicillin", "Recorded."],
                  cand("allergy", {"substance": "penicillin"}, "allergic to penicillin", entities=[P1])).results
    p.model.done()
    assert al.content == "Allergic to penicillin"
    with pytest.raises(PermissionDenied):
        p.cli_roles = None
        p.as_("ines").approve(al.id, fields={"reaction": "hives"})
    out = p.cli("approve", al.id, "--set", "reaction=hives and swelling", "--note", "checked the chart")
    assert (out["content"], out["review_note"]) == ("Allergic to penicillin (hives and swelling)", "checked the chart")
    history = p.cli("history", al.id)
    assert [(h["version"], h["status"], h["value"]) for h in history] == [
        (1, "archived", "Allergic to penicillin"), (2, "active", "Allergic to penicillin (hives and swelling)")]
    assert p.cli("approve", al.id, "--set", "nonsense", ok=False).count("KEY=VALUE") == 1


def test_c5_middleware_agent(p):
    from langchain.agents import create_agent

    p.push("ines", ["I like the lab values listed first", "OK."],
           cand("preference", {"key": "layout", "content": "Wants lab values listed first"},
                "I like the lab values listed first"))
    [dx] = _patient_note(p)
    p.model.done()
    p.admin("dr-silva").approve(dx.id)
    model = Agent(steps=[
        call("propose_memory", type="fact", fields={"content": "Ward B is closed on Sundays"},
             evidence=[{"quote": "ward B is closed on Sundays"}]),
        AIMessage(content="Noted; I listed the labs first."),
    ])
    agent = create_agent(model, middleware=[p.memory.middleware(workspace_id=WS, user_from=lambda ctx: ctx["user"])])
    out = agent.invoke({"messages": [HumanMessage("Remember that ward B is closed on Sundays")]},
                       context={"user": "ines"})
    system = next(m for m in model.seen[0] if isinstance(m, SystemMessage)).content
    assert "<about_the_user>" in system and "Wants lab values listed first" in system
    assert "type 2 diabetes" not in system  # a patient's memory needs a filter that names the patient
    assert out["memhub_injected"] and all("@" in ref for ref in out["memhub_injected"])
    proposal = json.loads(out["messages"][2].content)
    assert (proposal["event"], proposal["status"]) == ("PENDING", "candidate")
    assert [r["content"] for r in p.as_("ines").queue()] == ["Ward B is closed on Sundays"]
    assert p.as_("raul").get_all(type="preference") == []  # the snapshot is per user


def test_c6_medication_expires(p):
    old = (NOW - timedelta(days=20)).isoformat()
    [stale] = p.push("ines", [{"role": "user", "content": "Patient on amoxicillin 500 mg", "timestamp": old}],
                     cand("medication", {"drug": "amoxicillin", "dose": "500 mg"}, "Patient on amoxicillin 500 mg",
                          entities=[P1], durability="temporary"), thread="m1").results
    [current] = p.push("ines", ["Patient on metformin 1000 mg", "OK."],
                       cand("medication", {"drug": "metformin", "dose": "1000 mg"}, "Patient on metformin 1000 mg",
                            entities=[P1], valid_until=NOW + timedelta(days=90)), thread="m2").results
    p.model.done()
    doc = p.admin("dr-silva")
    for e in (stale, current):
        doc.approve(e.id)
    ines = p.as_("ines")
    meds = ines.search("patient medication takes", filters={"entities": [P1]}, top_k=10)
    assert p.contents(meds) == ["Takes metformin 1000 mg"]
    assert ines.get(current.id)["valid_until"].date() == (NOW + timedelta(days=90)).date()
    soon = ines.search("takes", filters={"entities": [P1], "valid_until": {"lte": (NOW + timedelta(days=30)).date()
                                                                           .isoformat()}})
    assert soon == []
    assert [r["content"] for r in p.cli("list", "--ws", WS, "--user", "ines", "--stale")] == ["Takes amoxicillin 500 mg"]
    assert "Takes amoxicillin 500 mg" in p.contents(p.cli(
        "search", "amoxicillin", "--ws", WS, "--user", "ines", "--include-stale", "--entity", "patient:MRN-0001"))


def test_c7_archive_history_delete(p):
    doc = p.admin("dr-silva")
    note = doc.add("The pharmacy closes at 6pm", scope="workspace").results[0]
    assert note.status == "active"  # an admin's own note needs nobody else
    doc.update(note.id, {"content": "The pharmacy closes at 7pm"})
    doc.delete(note.id)  # archive: kept in history
    assert p.as_("ines").search("pharmacy closes") == []
    assert [(h["version"], h["status"]) for h in p.cli("history", note.id)] == [(1, "superseded"), (2, "archived")]
    with pytest.raises(NotFound):
        p.as_("ines").update(note.id, {"content": "x"})

    ines = p.as_("ines")
    mine = ines.add("I park on level 2").results[0]
    with pytest.raises(PermissionDenied):
        p.memory.delete(mine.version_id, hard=True, **p.ids("raul"), roles=DOC)  # an admin may not erase her row
    ines.delete(mine.version_id, hard=True)
    assert ines.get_all() == []
    [cnd] = p.push("ines", ["The lift is broken today", "OK."],
                   cand("fact", "The lift on floor 3 is broken", "The lift is broken")).results
    doc.reject(cnd.id, note="temporary")
    doc.delete(cnd.version_id, hard=True)  # a rejected candidate may be cleaned up by an admin
    with pytest.raises(NotFound):
        ines.history(cnd.id)
    out = p.cli("history", "not-an-id", ok=False)
    assert "not a memhub id" in out and "Traceback" not in out


def test_c8_deepagents_personal_page(p):
    [first] = p.push("ines", ["I always start every round on ward B", "OK."],
                     cand("fact", "Starts every round on ward B", "start every round on ward B", area="care")).results
    assert first.status == "candidate"
    p.admin("dr-silva").approve(first.id)
    backend = deepagents_backend(p)
    me = "/memories/users/ines/care.md"
    p.model.say(cand("diagnosis", {"condition": "asthma"}, "I have asthma"))
    tools = deep_turn(p, backend, "ines", "I have asthma", call("read_file", file_path=me), append(me, "Has asthma"),
                      call("read_file", file_path=me))
    p.model.done()
    assert "Starts every round on ward B" in memory_section(tools[0])
    assert "pending review: asthma (needs review)" in tools[1]
    assert f"- [pending] asthma. Proposed {TODAY}." in tools[2]

    tools = deep_turn(p, backend, "ines", "thanks", call("read_file", file_path=me),
                      append(me, "Prefers night shifts"), call("read_file", file_path=me))
    assert "flag: Prefers night shifts (no supporting quote)" in tools[1]
    assert f"- [flag] Prefers night shifts. Noted {TODAY}." in tools[2]
    [flag] = [r for r in p.cli("queue", "--ws", WS) if r["status"] == "flag"]
    assert "a flag is not a memory" in p.cli("approve", flag["id"], ok=False)
    p.cli("reject", flag["id"], "--note", "never said")
    [other] = deep_turn(p, backend, "raul", "show me ines", call("read_file", file_path=me))
    assert "not found" in other


def test_c9_pages(p):
    for user, text, area in [("ines", "Starts every round on ward B", "care"),
                             ("ines", "Checks the crash cart every Monday", "care"),
                             ("ines", "Double-checks insulin doses with a colleague", "medication")]:
        [e] = p.push(user, [text, "OK."], cand("fact", text, text, area=area), thread=uuid.uuid4().hex).results
        p.admin("dr-silva").approve(e.id)
    p.model.done()
    ines = p.as_("ines")
    readme = ines.render()
    assert "care.md: Care (2 memories)" in readme and "medication.md: Medication (1 memories)" in readme
    care = ines.render("care")
    assert memory_section(care).count("[reviewed]") == 2 and "## How to read this" in care
    assert {pg["key"] for pg in ines.pages()} == {"care", "medication"}
    assert ines.page("Care")["title"] == "Care"
    with pytest.raises(NotFound):
        ines.render("cardiology")
    with pytest.raises(NotFound):
        p.as_("raul").page("care")
    printed = p.cli("page", "--ws", WS, "--user", "ines", "--area", "care")
    assert "Checks the crash cart every Monday" in str(printed)


def test_c10_schema_version(p, request):
    doctor = p.cli("doctor")
    assert "schema" in str(doctor).lower()
    assert p.cli("migrate", "--dry-run") is not None
    if p.backend_kind != "postgres":
        pytest.skip("a memhub 0.1.0 ledger is a Postgres dump (tests/data/v0_1_0_ledger.sql)")
    from tests.test_migrations import load_v0_1_0

    dsn = request.getfixturevalue("pg_dsn")
    prefix = f"v{uuid.uuid4().hex[:8]}"
    load_v0_1_0(dsn, prefix, model="FakeEmbeddings")  # what `Memory(embedder=FakeEmbeddings(16))` reports

    def old() -> Memory:
        return Memory(db=dsn, embedder=FakeEmbeddings(16), config={"project_prefix": prefix, "memory": None})

    ids = dict(workspace_id="acme", user_id="anna", run_id="r")
    with pytest.raises(SchemaOutdated, match="run `memhub migrate`"):
        old().search("inlet filter", **ids)
    assert old().migrate(dry_run=True) == ["0002_run_and_agent_ids"]
    assert old().migrate() == ["0002_run_and_agent_ids"]
    assert old().search("compressor C3 trips when the inlet filter is clogged", **ids)[0]["content"] == \
        "Compressor C3 trips when the inlet filter is clogged"
