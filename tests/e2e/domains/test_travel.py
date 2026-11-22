"""Domain 5, a corporate travel concierge: a deepagents agent keeps each traveller's memory as files under
`/memories/`, a LangChain agent books with memory in its prompt, and the travel desk reviews the shared policy.

The project: `strict: true` (closed areas, every type needs one), a keyed `preference` served in every prompt, a
`trip` that expires 60 days after it was said, a `document` that always needs review, a shared `policy`, and
`city` tags that group large areas into subject summaries.

    T1  strict: off-domain candidates and invented areas are dropped with a reason; hits carry their areas
    T2  the agent reads: folders, README, a page, grep and glob; another traveller's folder does not exist
    T3  the agent writes its own pages: a line said by the user is saved, an edit keeps the id, a removal archives
    T4  the team policy page: a line waits, the desk rejects it, the same line is refused next time
    T5  guards: an invented id, a header edit, a new area file, a delete, a stale whole-file write
    T6  a LangChain agent with `MemoryMiddleware`: preferences in the prompt, `search_memory` with filters
    T7  a keyed seat preference: the new value supersedes, the page and the prompt show only it
    T8  trips expire: stale trips leave search and pages; `observed_at` and `valid_until` filters
    T9  two travellers in one workspace: nothing of one is visible to the other; a document waits for the desk
    T10 a large area shows one summary per city; the agent opens a city's page
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from memhub import NotFound, PermissionDenied
from memhub.pipeline.extract import AreaRef, NewArea
from tests.e2e.domains.harness import (NOW, TODAY, Agent, append, call, cand, deep_turn, deepagents_backend,
                                       drop_line, last_tool, line_id, memory_section, plain, project, replace)

TRAVEL = """
---
strict: true
types:
  preference: {builtin: true, keyed: true, retrieval: always, keys: {seat: "seat on flights", diet: "meals", airline: "preferred airline", hotel: "hotel style"}}
  trip: {template: "Trip to {city} from {start} to {end}[ for {purpose?}]", ttl: 60d}
  document: {template: "{doc} expires {expires}", review: true}
  policy: {template: "{rule}", scopes: [workspace]}
  fact: builtin
areas:
  flights: airlines, seats, airports
  hotels: rooms, chains, loyalty
  dining: meals, diets, restaurants
  documents: passports, visas
  policy: company travel rules
entities:
  city: tag
---
# What to remember

Only what helps book travel: seats, meals, airlines, hotels, trips, travel documents and the company's policy.

- Never store anything else about the traveller: family, health, opinions, work outside travel.
"""

WS = "globex-travel"


@pytest.fixture(params=["sqlite", "postgres"])
def p(request, tmp_path, monkeypatch):
    return project(request, tmp_path, monkeypatch, name="travel", memory_md=TRAVEL, workspace=WS,
                   yaml_extra={"scopes": ["user", "workspace"], "render": {"summarize_over": 6}})


def pref(key, content, quote, area="flights"):
    return cand("preference", {"key": key, "content": content}, quote, area=area)


def seed(p):
    """Anna's aisle seat (flights) and vegetarian meals (dining); the team's per-diem policy (approved)."""
    p.push("anna", ["I always take an aisle seat", "OK.", "I eat vegetarian", "OK."],
           pref("seat", "Takes an aisle seat", "I always take an aisle seat"),
           pref("diet", "Eats vegetarian", "I eat vegetarian", area="dining"), thread="seed")
    [pol] = p.push("desk", ["Policy: economy class on flights under 6 hours", "OK."],
                   cand("policy", {"rule": "Economy class on flights under 6 hours"},
                        "economy class on flights under 6 hours", scope="workspace", area="policy"),
                   thread="seed-policy").results
    p.admin("desk").approve(pol.id)
    p.model.done()


ME, TEAM = "/memories/users/anna/flights.md", f"/memories/team/{WS}/policy.md"


def test_t1_strict_domain(p):
    invented = cand("fact", "Hotel loyalty status gold", "My hotel loyalty status is gold")
    invented.areas = [AreaRef(new=NewArea(title="Loyalty", description="points"))]
    out = p.push("anna", ["I always take an aisle seat", "OK.", "My daughter starts school in May", "Nice.",
                          "My hotel loyalty status is gold", "Congrats."],
                 pref("seat", "Takes an aisle seat", "I always take an aisle seat"),
                 cand("fact", "Daughter starts school in May", "My daughter starts school in May"),
                 invented)
    p.model.done()
    assert [(e.type, e.status) for e in out.results] == [("preference", "active")]
    assert sorted(d.reason for d in out.dropped) == ["invalid_fields", "no_area"]
    [hit] = p.as_("anna").search("aisle seat")
    assert hit["areas"] == ["Flights"]


def test_t2_reading(p):
    seed(p)
    backend = deepagents_backend(p)
    root, mine, readme, page, grep, globbed, other = deep_turn(
        p, backend, "anna", "what do you know about me?",
        call("ls", path="/memories/"), call("ls", path="/memories/users/anna/"),
        call("read_file", file_path="/memories/users/anna/README.md"), call("read_file", file_path=ME),
        call("grep", pattern="aisle", output_mode="content"), call("glob", pattern="**/*.md"),
        call("ls", path="/memories/users/bob/"))
    assert "/memories/users/" in root and "/memories/team/" in root
    assert "/memories/users/anna/flights.md" in mine and "/memories/users/anna/dining.md" in mine
    assert "flights.md: Flights (1 memories)" in readme and "dining.md: Dining (1 memories)" in readme
    assert '[said] "I always take an aisle seat"' in memory_section(page)
    assert '"I always take an aisle seat"' in grep
    for path in ("/memories/users/anna/flights.md", "/memories/users/anna/dining.md", TEAM):
        assert path in globbed
    assert "not found" in other


def test_t3_own_pages(p):
    seed(p)
    backend = deepagents_backend(p)
    p.model.say(pref("airline", "Flies TAP when possible", "I fly TAP when I can"))
    tools = deep_turn(p, backend, "anna", "I fly TAP when I can", call("read_file", file_path=ME),
                      append(ME, "Flies TAP when possible"), call("read_file", file_path=ME))
    assert re.search(r"saved #[0-9a-f]{8}: Flies TAP when possible", tools[1])
    assert '[said] "I fly TAP when I can"' in memory_section(tools[2])

    p.model.say(pref("seat", "Takes a window seat", "window seat from now on"))  # a page edit: no judge asked
    tools = deep_turn(p, backend, "anna", "window seat from now on", call("read_file", file_path=ME),
                      replace(ME, '"I always take an aisle seat"', "Takes a window seat"),
                      call("read_file", file_path=ME))
    seat = line_id(plain(tools[0]), "I always take an aisle seat")
    assert f"saved #{seat}: Takes a window seat" in tools[1]
    assert line_id(plain(tools[2]), "window seat from now on") == seat and "aisle" not in tools[2]

    tools = deep_turn(p, backend, "anna", "forget my airline", call("read_file", file_path=ME),
                      drop_line(ME, "I fly TAP"), call("read_file", file_path=ME))
    assert re.search(r"archived #[0-9a-f]{8}", tools[1]) and "TAP" not in memory_section(tools[2])
    p.model.done()
    assert sorted(p.contents(p.as_("anna").get_all(type="preference"))) == ["Eats vegetarian", "Takes a window seat"]


def test_t4_team_policy_page(p):
    seed(p)
    backend = deepagents_backend(p)
    line = "Hotels up to 180 EUR a night in Lisbon"
    p.model.say(cand("policy", {"rule": line}, "hotels up to 180 EUR a night in Lisbon", scope="workspace"))
    tools = deep_turn(p, backend, "anna", "the desk said hotels up to 180 EUR a night in Lisbon",
                      call("read_file", file_path=TEAM), append(TEAM, line), call("read_file", file_path=TEAM))
    assert "Economy class on flights under 6 hours" in memory_section(tools[0])
    assert f"pending review: {line}" in tools[1] and f"- [pending] {line}. Proposed {TODAY}." in tools[2]
    [queued] = p.cli("queue", "--ws", WS)
    p.cli("reject", queued["id"], "--note", "the cap is 150")
    p.model.say(cand("policy", {"rule": line}, "hotels up to 180 EUR a night in Lisbon", scope="workspace"))
    tools = deep_turn(p, backend, "anna", "hotels up to 180 EUR a night in Lisbon",
                      call("read_file", file_path=TEAM), append(TEAM, line))
    p.model.done()
    assert "refused: a reviewer rejected this before" in tools[1]
    assert p.cli("queue", "--ws", WS) == []
    [bobs] = deep_turn(p, backend, "bob", "policy?", call("read_file", file_path=TEAM))
    assert "Economy class" in bobs and "180 EUR" not in bobs


def test_t5_guards(p):
    seed(p)
    backend = deepagents_backend(p)

    def meanwhile(messages):  # the desk changes the policy page between the agent's read and its write
        [rule] = p.push("desk", ["Policy: book trains under 4 hours", "OK."],
                        cand("policy", {"rule": "Book trains under 4 hours"}, "book trains under 4 hours",
                             scope="workspace", area="policy"), thread="meanwhile").results
        p.admin("desk").approve(rule.id)
        return call("write_file", file_path=TEAM, content=plain(last_tool(messages)) + "Taxis need a receipt\n")

    tools = deep_turn(
        p, backend, "anna", "tidy up",
        call("read_file", file_path=ME),
        lambda m: call("write_file", file_path=ME, content=re.sub(r"#[0-9a-f]{8} ", "#deadbeef ",
                                                                  plain(last_tool(m)), count=1)),
        call("read_file", file_path=ME),
        lambda m: call("write_file", file_path=ME, content=plain(last_tool(m)).replace("# Memory: Flights (anna)",
                                                                                       "# My flights")),
        call("write_file", file_path="/memories/users/anna/cruises.md", content="Loves cruises\n"),
        call("read_file", file_path=TEAM), meanwhile)
    assert "refused: line #deadbeef id changed" in tools[1]
    assert tools[3].splitlines()[1:] == ["no change"]
    assert "areas come from memory.md" in tools[4]
    assert "page changed since you read it: read it again" in tools[6]
    assert backend.delete(ME).error == "areas come from memory.md: edit lines in an existing page"
    p.model.done()
    assert sorted(p.contents(p.as_("anna").get_all(type="preference"))) == ["Eats vegetarian", "Takes an aisle seat"]


def test_t6_middleware_agent(p):
    from langchain.agents import create_agent

    seed(p)
    model = Agent(steps=[
        call("search_memory", query="flight class", filters={"types": ["policy"]}),
        call("search_memory", query="x", filters={"areas": {"in": []}}),
        AIMessage(content="Booked an aisle seat in economy with a vegetarian meal."),
    ])
    mw = p.memory.middleware(workspace_id=WS, user_from=lambda ctx: ctx["user"], k=3)
    agent = create_agent(model, middleware=[mw])
    out = agent.invoke({"messages": [HumanMessage("Book my flight to Lisbon next month")]}, context={"user": "anna"})
    system = next(m for m in model.seen[0] if isinstance(m, SystemMessage)).content
    assert "Takes an aisle seat" in system and "Eats vegetarian" in system and "<about_the_user>" in system
    tools = [m.content for m in out["messages"] if isinstance(m, ToolMessage)]
    assert "Economy class on flights under 6 hours" in tools[0] and "aisle" not in tools[0]
    assert tools[1].startswith("invalid filters:")
    assert len(out["memhub_injected"]) >= 2
    bob = Agent(steps=[AIMessage(content="ok")])
    create_agent(bob, middleware=[mw]).invoke({"messages": [HumanMessage("Book a flight")]}, context={"user": "bob"})
    bob_system = next((m.content for m in bob.seen[0] if isinstance(m, SystemMessage)), "")
    assert "aisle" not in bob_system and "vegetarian" not in bob_system


def test_t7_keyed_seat(p):
    seed(p)
    p.model.judge("updates")
    [window] = p.push("anna", ["Window seat from now on, please", "OK."],
                      pref("seat", "Takes a window seat", "Window seat from now on"), thread="t7").results
    p.model.done()
    anna = p.as_("anna")
    assert window.event == "UPDATE"
    assert [(h["version"], h["status"], h["content"]) for h in anna.history(window.id)] == [
        (1, "superseded", "Takes an aisle seat"), (2, "active", "Takes a window seat")]
    page = anna.render("flights")
    assert '"Window seat from now on"' in memory_section(page) and "aisle" not in page
    snapshot = p.memory.middleware(workspace_id=WS, user_id="anna")._snapshot(WS, "anna")[0]
    assert "Takes a window seat" in snapshot and "aisle" not in snapshot


def test_t8_trips_expire(p):
    old = (NOW - timedelta(days=90)).isoformat()
    p.push("anna", [{"role": "user", "content": "I was in Madrid from 2026-08-01 to 2026-08-04 for a sales visit",
                     "timestamp": old}],
           cand("trip", {"city": "Madrid", "start": "2026-08-01", "end": "2026-08-04", "purpose": "a sales visit"},
                "Madrid from 2026-08-01 to 2026-08-04", area="hotels", entities=[{"type": "city", "id": "madrid"}]),
           thread="old")
    [lisbon] = p.push("anna", ["I go to Lisbon from 2026-12-02 to 2026-12-05", "OK."],
                      cand("trip", {"city": "Lisbon", "start": "2026-12-02", "end": "2026-12-05"},
                           "Lisbon from 2026-12-02 to 2026-12-05", area="hotels",
                           entities=[{"type": "city", "id": "lisbon"}]), thread="new").results
    p.model.done()
    anna = p.as_("anna")
    assert p.contents(anna.search("trip to", top_k=10)) == ["Trip to Lisbon from 2026-12-02 to 2026-12-05"]
    assert "Madrid" not in anna.render("hotels")
    assert [r["content"] for r in p.cli("list", "--ws", WS, "--user", "anna", "--stale")] == [
        "Trip to Madrid from 2026-08-01 to 2026-08-04 for a sales visit"]
    recent = anna.search("trip", filters={"observed_at": {"gte": (NOW - timedelta(days=1)).date().isoformat()}})
    assert p.contents(recent) == ["Trip to Lisbon from 2026-12-02 to 2026-12-05"]
    assert anna.search("trip", filters={"valid_until": {"lte": TODAY}}) == []  # stale rows never come back
    assert anna.get(lisbon.id)["valid_until"].date() == (NOW + timedelta(days=60)).date()


def test_t9_two_travellers(p):
    seed(p)
    [doc] = p.push("anna", ["My passport expires on 2027-03-01", "Noted."],
                   cand("document", {"doc": "Passport", "expires": "2027-03-01"}, "My passport expires on 2027-03-01",
                        area="documents"), thread="doc").results
    p.model.done()
    assert (doc.status, doc.content) == ("candidate", "Passport expires 2027-03-01")
    anna, bob = p.as_("anna"), p.as_("bob")
    [seat] = anna.get_all(type="preference", filters={"areas": "flights"})
    for action in (lambda: bob.get(seat["memory_id"]), lambda: bob.history(seat["memory_id"]),
                   lambda: bob.update(seat["memory_id"], {"content": "x"}), lambda: bob.render("flights"),
                   lambda: bob.approve(doc.id)):
        with pytest.raises(NotFound):
            action()
    assert bob.search("aisle seat vegetarian") == [] or all(h["scope"] == "workspace" for h in bob.search("aisle"))
    assert bob.queue() == [] and [r["type"] for r in anna.queue()] == ["document"]
    with pytest.raises(PermissionDenied, match="a document needs review by an approving role, not its owner"):
        anna.approve(doc.id)
    assert p.admin("desk").get(seat["memory_id"])["content"] == "Takes an aisle seat"  # the desk sees everyone's
    p.admin("desk").approve(doc.id)
    assert "Passport expires 2027-03-01" in anna.render("documents")
    assert bob.delete_all() == {"deleted": 0, "runs": 0} and anna.get(doc.id)["status"] == "active"


def test_t10_city_summaries(p):
    notes = {"lisbon": ["Lisbon Alfama inn has thin walls", "Lisbon Baixa suites include breakfast",
                        "Lisbon airport hotel runs a free shuttle", "Lisbon Chiado rooms face a noisy tram"],
             "porto": ["Porto Ribeira loft floods in winter", "Porto Boavista tower has a gym",
                       "Porto station hotel lacks parking", "Porto riverside rooms need early booking"],
             "madrid": ["Madrid Gran Via hostel is cash only"]}
    for city, texts in notes.items():
        for text in texts:
            p.push("anna", [text, "OK."], cand("fact", text, text, area="hotels",
                                               entities=[{"type": "city", "id": city}]), thread=uuid.uuid4().hex)
    p.model.done()
    backend = deepagents_backend(p)
    page, lisbon, missing = deep_turn(p, backend, "anna", "hotels?",
                                      call("read_file", file_path="/memories/users/anna/hotels.md"),
                                      call("read_file", file_path="/memories/users/anna/hotels/lisbon.md"),
                                      call("read_file", file_path="/memories/users/anna/hotels/berlin.md"))
    assert "#s_lisbon (4 memories)" in page and "→ hotels/lisbon.md" in page
    assert "#s_porto (4 memories)" in page
    assert "[said] \"Lisbon" not in page and "[said] \"Porto" not in page  # summarized, not listed
    assert sum(text in lisbon for text in notes["lisbon"]) == 4 and "Porto" not in lisbon
    assert "not found" in missing
    assert "Lisbon Baixa suites include breakfast" in p.as_("anna").render("hotels", subject="lisbon")
