"""Domain 4, a software team: developers use Claude Code and other agents; memhub keeps the team's conventions and
decisions, each developer's preferences, and knowledge about each repo.

The project: `repo` and `org` subjects (isolated), a `service` tag, a `convention` shared with the team, a
`decision` that always needs a reviewer, a keyed `preference` capped at two per developer, open areas.

    D1  Claude Code over MCP (in process): the tools' schemas, propose, review at the terminal, search
    D2  `memhub ingest --source claude_code` reads past Claude Code sessions of this folder
    D3  a developer works from the terminal only: `use`, `config show/set`, `add`, `list`, `search`, `doctor`
    D4  repo first, then the org, then general; version strings and incident ids found by keyword
    D5  a personal convention promoted to the team, approved with a note, used by a teammate's agent
    D6  inline review in an agent (`check_fields`, `propose(approve=True)`): a preference saved, a decision waits
    D7  over MCP: a proposal a reviewer rejected is refused again, unless the developer asks for it
    D8  caps: `max_active` refuses a third preference by hand and drops one from a chat (`cap_reached`)
    D9  filters through every surface: types, content, NOT, OR from the toolkit, MCP, CLI; errors on each
    D10 areas: a proposed area, `memhub areas`, `areas merge`, and search narrowed by area
"""
from __future__ import annotations

import json
import os

import anyio
import pytest
from mcp import Client

from memhub import EmbeddingMismatch, Memory, PermissionDenied, ValidationError
from memhub.mcp_server import build_server, folder_workspace
from memhub.pipeline.extract import AreaRef, NewArea
from tests.e2e.domains.harness import NOW, cand, project
from tests.fakes import FakeEmbeddings

DEVTEAM = """
---
types:
  fact: {builtin: true, area: optional}
  convention: {template: "{rule}[ (why: {why?})]", area: optional}
  decision: {template: "Decided {decision}: {why}", review: true, scopes: [workspace]}
  preference: {builtin: true, keyed: true, max_active: 2, keys: {linter: "linter", tests: "test framework", style: "code style"}}
areas:
  testing: tests, CI, fixtures
  tooling: linters, formatters, editors
entities:
  repo: isolate
  org: isolate
  service: tag
---
# What to remember

The team's conventions and decisions, each developer's tooling preferences, and facts about each repo.
"""

API = {"type": "repo", "id": "acme/api"}
WEB = {"type": "repo", "id": "acme/web"}
ORG = {"type": "org", "id": "acme"}


@pytest.fixture(params=["sqlite", "postgres"])
def p(request, tmp_path, monkeypatch):
    return project(request, tmp_path, monkeypatch, name="devteam", memory_md=DEVTEAM, workspace="acme-eng",
                   yaml_extra={"scopes": ["user", "workspace"]})


def mcp(server, *calls):
    async def run():
        async with Client(server) as client:
            out = []
            for name, args in calls:
                if name == "list_tools":
                    out.append({t.name: t.input_schema for t in (await client.list_tools()).tools})
                else:
                    result = await client.call_tool(name, args)
                    assert not result.is_error, result
                    out.append(json.loads(result.content[0].text))
            return out

    return anyio.run(run)


def test_d1_claude_code_over_mcp(p):
    ws = folder_workspace(p.folder)  # what `memhub mcp` uses in this folder, the way Claude Code names it
    p.workspace = ws
    server = build_server(p.memory, workspace_id=ws, user_id="joana", run_id="mcp-1")
    tools, proposal, bad = mcp(server, ("list_tools", {}),
                               ("propose_memory", {"type": "convention", "scope": "workspace",
                                                   "fields": {"rule": "Use ruff, never black", "why": "one tool"},
                                                   "evidence": [{"quote": "we use ruff, never black"}]}),
                               ("search_memory", {"query": "ruff", "filters": {"entities": {"type": "team"}}}))
    assert tools["propose_memory"]["properties"]["type"]["enum"] == ["fact", "convention", "decision", "preference"]
    assert (proposal["event"], proposal["status"], proposal["content"]) == (
        "PENDING", "candidate", "Use ruff, never black (why: one tool)")
    assert "error" in bad
    [queued] = p.cli("queue", "--ws", ws)
    assert (queued["agent_id"], queued["run_id"], queued["created_by"]) == ("mcp", "mcp-1", "mcp")
    p.cli("approve", proposal["id"])
    [hits] = mcp(server, ("search_memory", {"query": "ruff black linting"}))
    assert hits[0]["content"] == "Use ruff, never black (why: one tool)" and hits[0]["verified"] is True
    # one memory in the ledger: each keyword is in 100% of it, above `search.keyword_max_df`, so only vectors rank
    assert hits[0]["matched_by"] == "vector"


def test_d2_claude_code_sessions(p, monkeypatch):
    ws = folder_workspace(p.folder)
    sessions = p.folder.parent / "home" / ".claude" / "projects" / ws
    sessions.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(p.folder.parent / "home"))
    monkeypatch.setenv("MEMHUB_USER", "joana")
    said = (NOW.replace(microsecond=0) - __import__("datetime").timedelta(hours=3)).isoformat().replace("+00:00", "Z")

    def event(kind, uid, content):
        return {"type": kind, "sessionId": "s-1", "uuid": uid, "timestamp": said, "isSidechain": False,
                "message": {"role": kind, "content": content}}

    (sessions / "s-1.jsonl").write_text("\n".join(json.dumps(e) for e in [
        event("user", "u1", "In this repo every function has type hints, and tests use pytest, never unittest"),
        event("assistant", "a1", [{"type": "text", "text": "Got it."}]),
        event("assistant", "a2", [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]),
    ]) + "\n")
    p.model.say(cand("preference", {"key": "tests", "content": "Tests use pytest, never unittest"},
                     "tests use pytest, never unittest", area="testing"),
                cand("convention", {"rule": "Every function has type hints"}, "every function has type hints",
                     scope="workspace"))
    done = p.cli("ingest", "--source", "claude_code")
    p.model.done()
    assert (done["created"], done["failed_segments"]) == (2, 0)
    hits = p.cli("search", "pytest unittest", "--ws", ws, "--user", "joana")
    assert hits[0]["content"] == "Tests use pytest, never unittest" and hits[0]["evidence"][0]["source"] == "claude_code"
    [conv] = p.cli("queue", "--ws", ws)
    assert conv["content"] == "Every function has type hints" and conv["scope"] == "workspace"


def test_d3_terminal_only(p, tmp_path):
    assert p.cli("use", "acme-eng", "--user", "joana", config=False).strip() == "workspace=acme-eng user=joana"
    shown = p.cli("config", "show")
    assert "memory.md" in shown and "connect" in shown
    p.cli("config", "set", "search.keyword_weight", "1.5")
    assert "search.keyword_weight  1.5" in p.cli("config", "show")
    (p.folder / "pref.json").write_text(json.dumps({"key": "linter", "content": "Uses ruff for linting"}))
    added = p.cli("add", "--type", "preference", "--scope", "user", "-f", str(p.folder / "pref.json"))
    assert (added["user_id"], added["workspace_id"], added["status"]) == ("joana", "acme-eng", "active")
    again = p.cli("add", "--type", "preference", "--scope", "user", "-f", str(p.folder / "pref.json"), ok=False)
    assert "an active preference with key 'linter' exists: edit it instead" in again
    assert [r["content"] for r in p.cli("list") if r["type"] == "preference"] == ["Uses ruff for linting"]
    assert p.cli("search", "ruff linting")[0]["content"] == "Uses ruff for linting"
    doctor = p.cli("doctor")
    assert "schema" in doctor.lower() and "FAIL" not in doctor
    with pytest.raises(EmbeddingMismatch):  # another embedding model on the same ledger is refused
        Memory(config=p.yaml_path, embedder=FakeEmbeddings(32)).search("ruff", **p.ids("joana"))


def test_d4_repo_org_general(p):
    dev = p.as_("joana")
    lead = p.admin()
    api = lead.add({"content": "Release v2.3.1 of the API pinned pydantic 2.6", "entities": [API, ORG]},
                   scope="workspace").results[0]
    web = lead.add({"content": "Release v2.3.1 of the web app dropped IE11", "entities": [WEB, ORG]},
                   scope="workspace").results[0]
    org = lead.add({"content": "Every acme repo deploys on Fly.io; incident INC-4821 was a Fly outage",
                    "entities": [ORG]}, scope="workspace").results[0]
    gen = lead.add("Pin direct dependencies to minor versions", scope="workspace").results[0]
    hits = dev.search("release v2.3.1 pinned", filters={"entities": [API, ORG]}, widen=True, top_k=10)
    assert [(str(h["memory_id"]), h["matched_on"]) for h in hits] == [
        (api.id, "repo:acme/api"), (web.id, "org:acme"), (org.id, "org:acme"), (gen.id, "general")][:len(hits)]
    assert str(hits[0]["memory_id"]) == api.id and hits[-1]["matched_on"] == "general"
    assert str(dev.search("INC-4821", top_k=1)[0]["memory_id"]) == org.id
    assert dev.search("INC-4821", top_k=1)[0]["matched_by"] in ("keyword", "both")
    assert {str(h["memory_id"]) for h in dev.search("v2.3.1", top_k=2)} == {api.id, web.id}


def test_d5_promote_convention(p):
    [mine] = p.push("joana", ["I always squash merge my pull requests", "OK."],
                    cand("convention", {"rule": "Squash merge pull requests"}, "I always squash merge")).results
    assert (mine.scope, mine.status) == ("user", "active")
    promoted = p.as_("joana").promote(mine.id)
    assert (promoted.event, promoted.scope, promoted.status) == ("PENDING", "workspace", "candidate")
    with pytest.raises(PermissionDenied):
        p.as_("joana").approve(promoted.id)  # a workspace memory needs an approving role
    p.cli("approve", promoted.version_id, "--note", "agreed in the retro")
    kit = p.memory.tools(**p.ids("leo"))
    found = json.loads(kit.call("search_memory", {"query": "squash merge pull requests", "type": "convention"}))
    assert [(h["content"], h["scope"], h["verified"]) for h in found] == [
        ("Squash merge pull requests", "workspace", True)]
    p.model.done()


def test_d6_inline_review(p):
    dev = p.as_("joana")
    checked = dev.check_fields("decision", {"decision": "to use Postgres 16", "why": "pgvector"})
    assert checked["content"] == "Decided to use Postgres 16: pgvector"
    with pytest.raises(ValidationError, match="why"):
        dev.check_fields("decision", {"decision": "to use Postgres 16"})
    with pytest.raises(ValidationError, match="unknown key"):
        dev.check_fields("preference", {"key": "editor", "content": "Uses vim"})
    pref = dev.propose("preference", {"key": "style", "content": "Prefers small functions"}, approve=True,
                       evidence=[{"quote": "I like small functions"}])
    assert (pref.event, pref.status) == ("ADD", "active")
    dec = dev.propose("decision", {"decision": "to use Postgres 16", "why": "pgvector"}, scope="workspace",
                      approve=True)
    assert (dec.event, dec.status) == ("PENDING", "candidate")  # a developer may not approve a decision
    lead_dec = p.admin().propose("decision", {"decision": "to drop Python 3.10", "why": "3.11 typing"},
                                 scope="workspace", approve=True)
    assert lead_dec.status == "active"
    assert dev.get(pref.id)["verified"] is True


def test_d7_rejected_over_mcp(p):
    server = build_server(p.memory, workspace_id="acme-eng", user_id="joana", run_id="mcp-7")
    args = {"type": "fact", "scope": "workspace", "fields": {"content": "The staging database is wiped every Friday"}}
    [first] = mcp(server, ("propose_memory", args))
    p.cli("reject", first["id"], "--note", "not true any more")
    again, asked = mcp(server, ("propose_memory", args), ("propose_memory", {**args, "user_requested": True}))
    assert "rejected" in again and "reject" in again["rejected"]
    assert asked["status"] == "candidate" and asked["id"] != first["id"]
    assert [r["content"] for r in p.cli("queue", "--ws", "acme-eng")] == ["The staging database is wiped every Friday"]


def test_d8_caps(p):
    dev = p.as_("joana")
    dev.add({"key": "linter", "content": "Uses ruff"}, type="preference")
    dev.add({"key": "tests", "content": "Uses pytest"}, type="preference")
    with pytest.raises(ValidationError, match=r"the preference limit \(2\) is reached"):
        dev.add({"key": "style", "content": "Prefers small functions"}, type="preference")
    out = p.push("joana", ["I prefer small functions everywhere", "OK."],
                 cand("preference", {"key": "style", "content": "Prefers small functions"},
                      "I prefer small functions everywhere"))
    p.model.done()
    assert out.results == [] and [d.reason for d in out.dropped] == ["cap_reached"]
    assert p.as_("leo").add({"key": "style", "content": "Prefers small functions"}, type="preference").results


def test_d9_filters_everywhere(p):
    lead = p.admin()
    lead.add({"rule": "Every PR needs one approval", "entities": [API]}, type="convention", scope="workspace")
    lead.add({"rule": "Feature flags live in LaunchDarkly", "entities": [WEB]}, type="convention", scope="workspace")
    lead.add({"content": "The API's PR template asks for a test plan", "entities": [API]}, scope="workspace")
    flt = {"types": ["convention"], "NOT": {"content": {"contains": "launchdarkly"}}}
    kit = p.memory.tools(**p.ids("joana"))
    via_kit = json.loads(kit.call("search_memory", {"query": "PR", "filters": flt}))
    [via_mcp] = mcp(build_server(p.memory, workspace_id="acme-eng", user_id="joana", run_id="m"),
                    ("search_memory", {"query": "PR", "filters": json.dumps(flt)}))
    via_cli = p.cli("search", "PR", "--ws", "acme-eng", "--user", "joana", "--filter", json.dumps(flt))
    via_py = p.as_("joana").search("PR", filters=flt)
    assert [h["content"] for h in via_kit] == [h["content"] for h in via_mcp] == p.contents(via_cli) \
        == p.contents(via_py) == ["Every PR needs one approval"]
    either = p.as_("joana").search("PR", top_k=10, filters={"OR": [{"types": ["fact"]}, {"entities": [WEB]}]})
    assert sorted(p.contents(either)) == ["Feature flags live in LaunchDarkly", "The API's PR template asks for a test plan"]
    assert p.contents(p.cli("list", "--ws", "acme-eng", "--filter", json.dumps({"types": ["fact"]}))) == [
        "The API's PR template asks for a test plan"]
    bad = {"types": ["convention"], "colour": "red"}
    assert "error" in json.loads(kit.call("search_memory", {"query": "PR", "filters": bad}))
    assert "colour" in p.cli("search", "PR", "--ws", "acme-eng", "--filter", json.dumps(bad), ok=False)
    with pytest.raises(ValidationError, match="type is given both"):
        p.as_("joana").get_all(type="fact", filters={"types": ["fact"]})


def test_d10_areas(p):
    p.model.say(cand("fact", "CI runs on GitHub Actions", "CI runs on GitHub Actions", area="testing"))
    p.push("joana", ["CI runs on GitHub Actions", "OK."], thread="t1")
    proposed = cand("fact", "Fixtures live in tests/fixtures", "Fixtures live in tests/fixtures")
    proposed.areas = [AreaRef(new=NewArea(title="Test data", description="fixtures and sample files"))]
    p.model.say(proposed)
    p.push("joana", ["Fixtures live in tests/fixtures", "OK."], thread="t2")
    p.model.done()
    areas = {a["key"]: a for a in p.cli("areas", "--ws", "acme-eng", "--user", "joana")}
    assert set(areas) == {"testing", "test_data"} and areas["test_data"]["proposed"] is True
    dev = p.as_("joana")
    assert p.contents(dev.search("where", filters={"areas": "Test data"})) == ["Fixtures live in tests/fixtures"]
    assert p.contents(p.cli("list", "--ws", "acme-eng", "--user", "joana", "--area", "testing")) == [
        "CI runs on GitHub Actions"]
    merged = p.cli("areas", "merge", str(areas["test_data"]["memory_id"]), str(areas["testing"]["memory_id"]))
    assert merged
    after = {a["key"]: a["count"] for a in p.cli("areas", "--ws", "acme-eng", "--user", "joana")}
    assert after == {"testing": 2}
    assert sorted(p.contents(dev.search("CI fixtures", filters={"areas": {"in": ["testing"]}}, top_k=10))) == [
        "CI runs on GitHub Actions", "Fixtures live in tests/fixtures"]
    assert dev.search("CI", filters={"areas": "no-such-area"}) == []
    assert os.path.exists(p.yaml_path)
