"""Domain 1, plant maintenance: technicians on a pump plant keep faults, fixes and readings about machines.

The project: `machine` and `model` subjects (isolated), `line` and `part` tags, fault codes searched by keyword, a
`fix` that a supervisor always reviews, a `fault` shared at once, readings that expire after a week.

    P1  the same words about two pumps are two memories; the same pump again merges
    P2  hybrid search finds fault codes and tags that embeddings miss; stopwords and hybrid off
    P3  filters: id prefix, OR, NOT, areas, content, observed_at, from Python and the CLI; bad filters refused
    P4  widen: the pump first, then its model, then general knowledge, top_k across tiers
    P5  review per type: a fault is shared at once, a fix waits even in user scope and its owner cannot approve it
    P6  a supervisor corrects a fix while approving it; history keeps the proposal; a bad correction changes nothing
    P7  shift handover through a deepagents agent: a team page line waits, is approved, and the next shift reads it
    P8  readings expire: stale ones leave search and the agent's page, `list --stale` and `--include-stale` show them
    P9  a keyed preference: a new value supersedes, a contradiction waits and is resolved with `--resolve`
    P10 a JSONL work-order log through `memhub ingest` with a dry run first, then `runs`, `areas` and `page`
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from memhub import NotFound
from memhub.service import PermissionDenied, ValidationError
from tests.e2e.domains.harness import NOW, TODAY, cand, call, deep_turn, deepagents_backend, append, project

PLANT = """
---
types:
  fact: {builtin: true, area: optional}
  fault: {template: "{code}: {symptom}", review: false, area: optional}
  fix: {template: "{code} fixed by {action}[ ({minutes:int?} min)]", review: true, area: optional}
  reading: {template: "{quantity} {value:float} {unit}", ttl: 7d}
  preference: {builtin: true, keyed: true, keys: {units: "metric or imperial units", detail: "how long the answers are"}}
areas:
  bearings: bearings, vibration, lubrication
  electrical: motors, drives, breakers, fault codes
  hydraulics: pumps, seals, valves, cavitation, pressure
entities:
  machine: isolate
  model: isolate
  line: tag
  part: tag
---
# What to remember

Faults, fixes and readings about the plant's machines, and how each technician wants answers.

- A fault has its code and what was observed; a fix has the code it fixed and what was done.
- Never keep greetings, questions, or anything only the assistant said.
"""

WS = "plant-a"
P101 = {"type": "machine", "id": "plant-a/line-2/P-101"}
P102 = {"type": "machine", "id": "plant-a/line-2/P-102"}
CR32 = {"type": "model", "id": "grundfos-cr-32"}


@pytest.fixture(params=["sqlite", "postgres"])
def p(request, tmp_path, monkeypatch):
    return project(request, tmp_path, monkeypatch, name="plant", memory_md=PLANT, workspace=WS,
                   yaml_extra={"scopes": ["user", "workspace"], "search": {"stopwords": ["pump", "pumps"]}})


def test_p1_same_words_two_pumps(p):
    said = "Pump vibrates above 7 mm/s after the night shift"
    [a] = p.push("marco", [said, "Logged."], cand("fact", said, said, entities=[P102]), thread="s1",
                 entities=[P101, CR32]).results
    [b] = p.push("marco", [said, "Logged."], cand("fact", said, said), thread="s2", entities=[P102, CR32]).results
    [c] = p.push("marco", [said, "Logged again."], cand("fact", said, said), thread="s3", entities=[P101, CR32]).results
    p.model.done()
    assert (a.event, b.event) == ("ADD", "ADD") and a.id != b.id
    assert (c.event, c.id) == ("MERGE", a.id)
    me = p.as_("marco")
    row = me.get(a.id)
    assert {(e["type"], e["id"]) for e in row["entities"]} == {("machine", P101["id"]), ("model", CR32["id"])}
    assert len([e for e in row["evidence"] if e.get("quote") == said]) == 2  # one memory, two shifts of evidence
    assert [h["version"] for h in me.history(a.id)] == [1]


def test_p2_hybrid_finds_codes(p):
    lead = p.admin()
    for code, symptom in [("E47", "low suction pressure lockout"), ("E74", "high discharge temperature"),
                          ("E470", "drive overcurrent trip"), ("P-101", "seal weeps after a cold start")]:
        (p.folder / "f.json").write_text(json.dumps({"code": code, "symptom": symptom}))
        p.cli("add", "--type", "fault", "--scope", "workspace", "-f", str(p.folder / "f.json"), "--ws", WS)
    for i in range(6):
        lead.add(f"Pump {i} on line 2 was greased on schedule", scope="workspace")

    hits = p.as_("lena").search("E47", top_k=3)
    assert hits[0]["content"] == "E47: low suction pressure lockout" and hits[0]["matched_by"] in ("keyword", "both")
    assert all(h["content"] != "E470: drive overcurrent trip" or h["matched_by"] == "vector" for h in hits)
    top = p.as_("lena").search("seal on P-101", top_k=1)[0]
    assert (top["content"], top["matched_by"]) == ("P-101: seal weeps after a cold start", "both")
    assert all("matched_by" not in h for h in p.as_("lena").search("E47", hybrid=False))
    cli_hits = p.cli("search", "E74", "--ws", WS, "--user", "lena")
    assert cli_hits[0]["content"] == "E74: high discharge temperature" and cli_hits[0]["matched_by"] != "vector"
    # `pump` is a project stopword: a query of it alone has no keyword, and the result is the vector search's
    assert {h["matched_by"] for h in p.as_("lena").search("pump", top_k=10)} == {"vector"}
    with pytest.raises(ValidationError, match="hybrid must be True, False or None"):
        p.as_("lena").search("E47", hybrid="yes")


def test_p3_filters(p):
    lead = p.admin()
    old = (NOW - timedelta(days=40)).isoformat()
    rows = {}
    for mid, text, ents in [
        ("PRD_1", "Gearbox on PRD_1 runs hot at full load", [{"type": "machine", "id": "PRD_1"}]),
        ("PRD_2", "Gearbox on PRD_2 leaks oil at the shaft seal", [{"type": "machine", "id": "PRD_2"}]),
        ("PRD_10", "Gearbox on PRD_10 was rebuilt with code E47 logged", [{"type": "machine", "id": "PRD_10"}]),
        ("L2", "Line 2 conveyor gearbox squeals", [{"type": "line", "id": "L2"}]),
    ]:
        rows[mid] = lead.add({"content": text, "entities": ents}, scope="workspace").results[0].id
    p.push("marco", [{"role": "user", "content": "Gearbox on PRD_3 had a cracked housing last month", "timestamp": old}],
           cand("fact", "Gearbox on PRD_3 had a cracked housing", "Gearbox on PRD_3 had a cracked housing",
                entities=[{"type": "machine", "id": "PRD_3"}]), thread="old")
    p.model.done()
    me = p.as_("marco")

    def found(flt, **kw):
        return sorted(h["content"].split(" ")[2] for h in me.search("gearbox", filters=flt, top_k=10, **kw))

    assert found({"entities": {"type": "machine", "id": {"prefix": "PRD_1"}}}) == ["PRD_1", "PRD_10"]
    assert found({"entities": {"type": "machine", "id": {"in": ["PRD_2", "PRD_3"]}}}) == ["PRD_2", "PRD_3"]
    assert found({"OR": [{"entities": [{"type": "machine", "id": "PRD_2"}]}, {"entity_type": "line"}]}) == ["PRD_2", "conveyor"]
    assert found({"entity_type": "machine", "NOT": {"content": {"contains": "e47"}}}) == ["PRD_1", "PRD_2", "PRD_3"]
    assert found({"entity_type": "machine", "observed_at": {"lte": (NOW - timedelta(days=30)).date().isoformat()}}) \
        == ["PRD_3"]
    assert found({"entity_type": "machine", "observed_at": {"gte": TODAY}}) == ["PRD_1", "PRD_10", "PRD_2"]
    hits = me.search("gearbox", filters={"entities": [{"type": "machine", "id": {"prefix": "PRD_"}}]}, top_k=10)
    assert {h["matched_on"] for h in hits} == {"machine:PRD_1", "machine:PRD_2", "machine:PRD_3", "machine:PRD_10"}
    # the same filter from the CLI, and `--entity` as its short form
    cli = p.cli("search", "gearbox", "--ws", WS, "--user", "marco",
                "--filter", json.dumps({"entities": {"type": "machine", "id": {"prefix": "PRD_1"}}}))
    assert sorted(h["content"].split(" ")[2] for h in cli) == ["PRD_1", "PRD_10"]
    assert [h["matched_on"] for h in p.cli("search", "gearbox", "--ws", WS, "--user", "marco",
                                           "--entity", "machine:PRD_2")] == ["machine:PRD_2"]
    listed = p.cli("list", "--ws", WS, "--filter", json.dumps({"content": {"contains": "LEAKS"}}))
    assert [r["content"] for r in listed] == ["Gearbox on PRD_2 leaks oil at the shaft seal"]
    # bad filters are refused before any database call, with the reason
    for bad, why in [({"entities": {"type": "robot", "id": "R1"}}, "robot"), ({"OR": []}, "OR"),
                     ({"observed_at": {"gte": "last week"}}, "ISO"), ({"colour": "red"}, "colour")]:
        with pytest.raises(ValidationError, match=why):
            me.search("gearbox", filters=bad)
    out = p.cli("search", "gearbox", "--ws", WS, "--filter", '{"entities": ', ok=False)
    assert "not valid JSON" in out


def test_p4_widen_tiers(p):
    lena = p.as_("lena")
    a = lena.add({"content": "Pump P-101 bearing replaced after vibration alarm", "entities": [P101, CR32]}).results[0]
    b = lena.add({"content": "Pump P-102 bearing replaced after vibration alarm", "entities": [P102, CR32]}).results[0]
    c = lena.add({"content": "CR-32 pumps need the seal checked every 4000 hours", "entities": [CR32]}).results[0]
    g = lena.add("Vibration above 7 mm/s usually means a worn bearing").results[0]
    hits = lena.search("bearing vibration", filters={"entities": [P101, CR32]}, widen=True, top_k=10)
    assert [str(h["memory_id"]) for h in hits][0] == a.id
    assert [h["matched_on"] for h in hits] == [f"machine:{P101['id']}", f"model:{CR32['id']}", f"model:{CR32['id']}",
                                               "general"]
    assert {str(h["memory_id"]) for h in hits[1:3]} == {b.id, c.id} and str(hits[3]["memory_id"]) == g.id
    assert [h["matched_on"] for h in lena.search("bearing", filters={"entities": [P101, CR32]}, widen=True, top_k=2)] \
        == [f"machine:{P101['id']}", f"model:{CR32['id']}"]  # top_k cuts across tiers, the pump's own first
    assert [str(h["memory_id"]) for h in lena.search("bearing", filters={"entities": [P101]})] == [a.id]
    assert "matched_on" not in lena.search("bearing")[0]
    with pytest.raises(ValidationError, match="widen"):
        lena.search("bearing", widen=True)  # widen needs the subjects to widen from
    fleet = lena.search("bearing", filters={"entity_type": "machine"}, top_k=10)
    assert {str(h["memory_id"]) for h in fleet} == {a.id, b.id}


def test_p5_review_per_type(p):
    msgs = ["E47 again on P-101: low suction pressure", "Noted.",
            "Fixed it by replacing the liquid-line filter drier", "Good.",
            "The suction strainer on line 2 clogs every spring", "Thanks."]
    out = p.push("marco", msgs,
                 cand("fault", {"code": "E47", "symptom": "low suction pressure"}, "E47 again on P-101: low suction pressure",
                      scope="workspace", area="electrical", entities=[P101]),
                 cand("fix", {"code": "E47", "action": "replacing the liquid-line filter drier"},
                      "Fixed it by replacing the liquid-line filter drier", area="hydraulics", entities=[P101]),
                 cand("fact", "The suction strainer on line 2 clogs every spring",
                      "The suction strainer on line 2 clogs every spring", scope="workspace"))
    p.model.done()
    by = {e.type: e for e in out.results}
    assert (by["fault"].event, by["fault"].status, by["fault"].scope) == ("ADD", "active", "workspace")
    assert (by["fix"].event, by["fix"].status, by["fix"].scope) == ("PENDING", "candidate", "user")
    assert (by["fact"].event, by["fact"].status) == ("PENDING", "candidate")
    assert p.as_("lena").get(by["fault"].id)["reviewed_by"] == "auto"  # shared with no person in between

    marco = p.as_("marco")
    with pytest.raises(PermissionDenied, match="a fix needs review by an approving role, not its owner"):
        marco.approve(by["fix"].id)
    with pytest.raises(PermissionDenied):
        marco.reject(by["fix"].id)
    assert {r["type"] for r in marco.queue()} == {"fix", "fact"}       # his own fix and the workspace's fact
    assert {r["type"] for r in p.as_("lena").queue()} == {"fact"}      # another technician sees the shared one only
    shown = p.cli("config", "show")
    for line in ("review.fix", "review.fault", "review.fact"):
        assert line in str(shown)

    queued = {r["type"]: r for r in p.cli("queue", "--ws", WS)}
    approved = p.cli("approve", queued["fix"]["id"], "--note", "checked on site")
    assert approved["status"] == "active" and approved["reviewed_by"].startswith("cli:")
    assert marco.search("liquid-line filter drier", top_k=1)[0]["content"] == \
        "E47 fixed by replacing the liquid-line filter drier"
    assert "liquid-line" not in " ".join(p.contents(p.as_("lena").search("liquid-line filter drier")))  # still his


def test_p6_review_edit(p):
    out = p.push("marco", ["Reset the drive and it ran", "OK."],
                 cand("fix", {"code": "E470", "action": "reset the drive"}, "Reset the drive and it ran"))
    [fix] = out.results
    p.model.done()
    lead = p.admin("dana")
    with pytest.raises(ValidationError):
        lead.approve(fix.id, fields={"minutes": "about an hour"})  # not an int: refused, nothing written
    assert [h["status"] for h in lead.history(fix.id)] == ["candidate"]
    out = p.cli("approve", fix.id, "--set", "action=replaced the drive fan", "--set", "minutes=45")
    assert out["status"] == "active" and out["content"] == "E470 fixed by replaced the drive fan (45 min)"
    history = p.cli("history", fix.id)
    assert [(h["version"], h["status"]) for h in history] == [(1, "archived"), (2, "active")]
    assert history[0]["value"] == "E470 fixed by reset the drive"
    assert history[1]["evidence"][0]["quote"] == "Reset the drive and it ran"  # the evidence is kept
    assert p.as_("marco").get(fix.id)["payload"]["minutes"] == 45


def test_p7_shift_handover_deepagents(p):
    p.push("lead", ["P-101 seal weeps after a cold start", "Noted."],
           cand("fault", {"code": "P-101", "symptom": "seal weeps after a cold start"},
                "P-101 seal weeps after a cold start", scope="workspace", area="hydraulics"), roles=["workspace_admin"])
    backend = deepagents_backend(p)
    team = f"/memories/team/{WS}/hydraulics.md"
    p.model.say(cand("fact", "Pump P-102 cavitates when the suction valve is under half open",
                     "P-102 cavitates when the suction valve is under half open", scope="workspace"))
    readme, page, wrote, after = deep_turn(
        p, backend, "marco", "P-102 cavitates when the suction valve is under half open",
        call("read_file", file_path=f"/memories/team/{WS}/README.md"), call("read_file", file_path=team),
        append(team, "Pump P-102 cavitates when the suction valve is under half open"),
        call("read_file", file_path=team))
    p.model.done()
    assert "hydraulics.md" in readme and "seal weeps" in page
    assert "pending review: Pump P-102 cavitates" in wrote
    assert "cavitates" not in after.split("## Pending review")[0] and "[pending] Pump P-102 cavitates" in after

    [queued] = [r for r in p.cli("queue", "--ws", WS) if "cavitates" in r["content"]]
    p.cli("approve", queued["id"], "--note", "seen on the night round")
    [next_shift] = deep_turn(p, backend, "lena", "anything on the pumps?", call("read_file", file_path=team))
    assert "[reviewed] Pump P-102 cavitates when the suction valve is under half open" in next_shift
    assert "## Pending review" not in next_shift  # marco's pending line is his, not lena's


def test_p8_readings_expire(p):
    old = (NOW - timedelta(days=10)).isoformat()
    [stale] = p.push("marco", [{"role": "user", "content": "Bearing temperature on P-101 was 82.5 C", "timestamp": old}],
                     cand("reading", {"quantity": "P-101 bearing temperature", "value": 82.5, "unit": "C"},
                          "Bearing temperature on P-101 was 82.5 C"), thread="r1").results
    [fresh] = p.push("marco", ["Bearing temperature on P-101 is 61 C now", "Logged."],
                     cand("reading", {"quantity": "P-101 bearing temperature now", "value": 61, "unit": "C"},
                          "Bearing temperature on P-101 is 61 C now"), thread="r2").results
    p.model.done()
    me = p.as_("marco")
    assert me.get(fresh.id)["valid_until"] is not None
    found = p.contents(me.search("P-101 bearing temperature", top_k=10))
    assert "P-101 bearing temperature now 61.0 C" in found and "P-101 bearing temperature 82.5 C" not in found
    assert [r["content"] for r in p.cli("list", "--user", "marco", "--ws", WS, "--stale")] == \
        ["P-101 bearing temperature 82.5 C"]
    assert "P-101 bearing temperature 82.5 C" in p.contents(
        p.cli("search", "P-101 bearing temperature", "--ws", WS, "--user", "marco", "--include-stale"))
    expiring = me.search("bearing temperature", filters={"valid_until": {"gte": TODAY}}, top_k=10)
    assert p.contents(expiring) == ["P-101 bearing temperature now 61.0 C"]
    assert stale.id != fresh.id


def test_p9_keyed_preference(p):
    [metric] = p.push("marco", ["Give me everything in metric units please", "Sure."],
                      cand("preference", {"key": "units", "content": "Wants metric units"},
                           "Give me everything in metric units please"), thread="c1").results
    p.model.judge("updates")
    [imperial] = p.push("marco", ["Actually switch me to imperial units", "Done."],
                        cand("preference", {"key": "units", "content": "Wants imperial units"},
                             "Actually switch me to imperial units"), thread="c2").results
    assert (imperial.event, imperial.id) == ("UPDATE", metric.id)
    me = p.as_("marco")
    assert [(h["version"], h["status"]) for h in me.history(metric.id)] == [(1, "superseded"), (2, "active")]

    me.update(metric.id, {"content": "Wants imperial units, temperatures in C"})  # a person's edit: verified
    [back] = p.push("marco", ["Hmm, metric units again", "OK."],
                    cand("preference", {"key": "units", "content": "Wants metric units again"},
                         "Hmm, metric units again"), thread="c3").results
    p.model.done()
    assert (back.event, back.status, back.conflicts_with) == ("CONFLICT", "candidate", metric.id)
    assert me.get(metric.id)["content"] == "Wants imperial units, temperatures in C"  # the edit is never overwritten
    [first] = p.cli("queue", "--ws", WS)
    assert first["conflicts_with"] == metric.id
    kept = p.cli("approve", first["id"], "--resolve", "replace")
    # `replace`: the candidate becomes the active row of the slot under its own id; the old memory is archived
    assert (kept["memory_id"], kept["status"], kept["content"]) == (back.id, "active", "Wants metric units again")
    assert [h["status"] for h in me.history(metric.id)] == ["superseded", "superseded", "archived"]
    with pytest.raises(NotFound):
        me.get(metric.id)
    assert [r["content"] for r in me.get_all(type="preference")] == ["Wants metric units again"]


def test_p10_jsonl_worklog_ingest(p):
    log = p.folder / "workorders.jsonl"
    said = (NOW - timedelta(hours=3)).isoformat()
    log.write_text("\n".join(json.dumps(r) for r in [
        {"wo": "WO-881", "tech": "marco", "site": WS, "n": "1", "at": said,
         "note": "Compressor C3 trips when the inlet filter is clogged", "reply": "Logged on WO-881."},
        {"wo": "WO-881", "tech": "marco", "site": WS, "n": "2", "at": said,
         "note": "Good morning team", "reply": "Morning!"},
        {"broken": True},
    ]) + "\n")
    with p.yaml_path.open("a") as f:
        f.write("sources:\n  workorders:\n    kind: jsonl\n    path: workorders.jsonl\n    one_line_per: turn\n"
                "    fields: {thread_id: wo, user_id: tech, workspace_id: site, message_id: n, timestamp: at,"
                " user_content: note, assistant_content: reply}\n")
    clogged = cand("fact", "Compressor C3 trips when the inlet filter is clogged",
                   "Compressor C3 trips when the inlet filter is clogged", area="electrical")
    p.model.say(clogged, cand("fact", "Pump P9 is fine", "Pump P9 is fine"))  # nobody said that: ungrounded
    preview = p.cli("ingest", "--source", "workorders", "--dry-run")
    written = [w for w in preview["preview"] if w["outcome"] != "dropped"]
    assert [(w["outcome"], w["content"], w["user_id"]) for w in written] == [
        ("created", clogged.fields["content"], "marco")]
    assert {d["reason"] for d in preview["preview"] if d["outcome"] == "dropped"} >= {"ungrounded"}
    assert p.cli("list", "--ws", WS) == []  # a dry run writes nothing

    p.model.say(clogged)
    done = p.cli("ingest", "--source", "workorders")
    p.model.done()
    assert done["created"] == 1 and done["failed_segments"] == 0
    [row] = [r for r in p.cli("list", "--ws", WS, "--user", "marco") if r["type"] == "fact"]
    assert row["content"] == clogged.fields["content"] and row["evidence"][0]["thread_id"] == "WO-881"
    assert p.cli("ingest", "--source", "workorders")["created"] == 0  # the watermark: nothing read twice
    runs = p.cli("runs")
    assert runs and runs[0]["source"] == "workorders"
    assert [(a["key"], a["count"]) for a in p.cli("areas", "--ws", WS, "--user", "marco")] == [("electrical", 1)]
    assert "Compressor C3 trips" in str(p.cli("page", "--ws", WS, "--user", "marco", "--area", "electrical"))
    with pytest.raises(NotFound):
        p.as_("lena").get(row["memory_id"])
