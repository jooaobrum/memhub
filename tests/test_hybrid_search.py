"""Hybrid search (issue #26): a keyword leg next to the vector leg, fused by weighted reciprocal rank.

The backends' keyword SQL is proven by the contract cases (`memhub/storage/contract.py`, `test_keyword_search_*`,
`test_hybrid_*`); these tests prove the rules (`memhub.storage.rules`) and the service on the in-memory backend,
with fake models and no Docker."""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from memhub import Memory, ValidationError, cli
from memhub.config import ConfigError, EntityConfig, SearchConfig
from memhub.settings import resolve
from memhub.storage.memory import _Tx
from memhub.storage.rules import fold, fuse, keyword_rank, query_keywords, tokens

ROOT = Path(__file__).resolve().parents[1]
WS, ALICE = "ws", dict(workspace_id="ws", user_id="alice", run_id="r")
DIMS = 16
_count = iter(range(10_000))


def unit(sim: float) -> list[float]:
    """A unit vector whose cosine to `unit(1.0)` is `sim`."""
    v = [0.0] * DIMS
    v[0], v[1] = sim, math.sqrt(max(0.0, 1 - sim**2))
    return v


class QueryAtOne:
    """Every query embeds to `unit(1.0)`; a seeded row's similarity is the one `seed` gives it."""

    def __init__(self) -> None:
        self.calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return unit(1.0)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [unit(1.0) for _ in texts]


def make(settings, search: SearchConfig | None = None, **entities) -> Memory:
    config = settings.model_copy(update={
        "workspace_default": WS, "entity_types": [],
        "entities": {"machine": EntityConfig(), "patient": EntityConfig(require_filter=True), **entities},
        **({"search": search} if search is not None else {})})
    m = Memory(db=f"memory://hybrid-{next(_count)}", embedder=QueryAtOne(), config=config)
    m.init()
    return m


@pytest.fixture
def m(settings) -> Memory:
    return make(settings)


def seed(m: Memory, content: str, sim: float, entities=(), **kw) -> dict:
    with m.store.unit_of_work() as uow:
        return uow.memories.add_memory(
            type="fact", schema_version=1, scope="user", workspace_id=WS, user_id="alice", content=content,
            payload={"content": content}, entities=list(entities), embedding=unit(sim), evidence=[], status="active",
            verified=True, created_by="test", **kw)


def row(content: str, sim: float = 0.5, mid: str | None = None) -> dict:
    return {"memory_id": mid or content, "content": content, "similarity": sim}


def names(rows) -> list[str]:
    return [r["content"] for r in rows]


def hits(query: str, content: str) -> bool:
    ranked, _ = keyword_rank([row(content)], query_keywords(query), searchable=10, max_df=1.0, limit=10)
    return bool(ranked)


# --- S1 ------------------------------------------------------------------------------------------------------------

def test_whole_token():
    """C3 (AC 3)."""
    assert not hits("E47", "error E470 on the unit")
    assert not hits("PRD_1", "belt replaced on PRD_10")
    assert hits("P-101", "seal replaced on P-101.")
    assert not hits("6205", "bearing 6205-2RS fitted")
    assert hits("pompe", "Pompe à chaleur en panne")
    assert hits("prefere", "Il préfère le thé")
    # the positive controls: the same keywords on their own token
    assert hits("E47", "fault E47 on the unit") and hits("PRD_1", "bearing on PRD_1") and hits("6205", "a 6205 bearing")


def test_tokenise():
    """C4 (AC 4)."""
    assert tokens("l'usine") == ["l", "usine"]
    assert query_keywords("l'usine") == ["usine"]
    assert query_keywords("dell'impianto") == ["impianto"]
    assert tokens("v2.3.1,") == ["v2.3.1"]
    assert tokens("(E47)") == ["e47"]
    assert tokens("bearing 6205-2RS on PRD_1/B") == ["bearing", "6205-2rs", "on", "prd_1/b"]
    assert fold("PRÉFÈRE Ça") == "prefere ca"


def test_keyword_hits_keep_filter_and_guard(settings):
    """C7 (AC 5): the keyword leg runs with the tier's filter and the `require_filter` guard."""
    m = make(settings, SearchConfig(vector_weight=0.0))
    p1, prd1 = {"type": "patient", "id": "p1"}, {"type": "machine", "id": "PRD_1"}
    seed(m, "patient p1 had E47", 0.1, [p1])
    seed(m, "PRD_2 shows E47", 0.1, [{"type": "machine", "id": "PRD_2"}])
    seed(m, "PRD_1 shows E47", 0.1, [prd1])
    seed(m, "nothing to see", 0.9)
    for i in range(3):  # each tier holds rows without the code, so `keyword_max_df` keeps it
        seed(m, f"PRD_1 belt {i}", 0.9, [prd1])
        seed(m, f"patient p1 visit {i}", 0.9, [p1])
    assert set(names(m.search("E47", top_k=10, **ALICE))) == {"PRD_2 shows E47", "PRD_1 shows E47"}
    patient = m.search("E47", filters={"entities": [{"type": "patient", "id": "p1"}]}, top_k=10, **ALICE)
    assert names(patient) == ["patient p1 had E47"]
    machine = m.search("E47", filters={"entities": [{"type": "machine", "id": "PRD_1"}]}, top_k=10, **ALICE)
    assert names(machine) == ["PRD_1 shows E47"]


def test_keyword_hits_keep_a_time_filter(settings):
    """C23 (AC 5): a filter that is not about subjects (`observed_at`) narrows the keyword leg too."""
    from datetime import datetime, timezone

    m = make(settings, SearchConfig(vector_weight=0.0))
    seed(m, "recent fault E47", 0.1, observed_at=datetime(2026, 10, 20, tzinfo=timezone.utc))
    seed(m, "old fault E47", 0.1, observed_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    for i in range(4):
        seed(m, f"other note {i}", 0.9, observed_at=datetime(2026, 10, 21, tzinfo=timezone.utc))
    assert set(names(m.search("E47", top_k=10, **ALICE))) == {"recent fault E47", "old fault E47"}
    rows = m.search("E47", filters={"observed_at": {"gte": "2026-10-01"}}, top_k=10, **ALICE)
    assert names(rows) == ["recent fault E47"]


# --- S2 ------------------------------------------------------------------------------------------------------------

def test_stopwords_four_languages():
    """C8 (AC 6): one or more per language - it `della`, fr `nous`, en `which`, pt `não` - and the plan's examples."""
    assert query_keywords("the le de il que do E und E47") == ["und", "e47"]
    assert query_keywords("della nous which não pompa") == ["pompa"]


def test_config_stopwords(settings):
    """C9 (AC 7): a domain stopword, compared after casefolding and accent folding."""
    assert query_keywords("maquina E47", stopwords=["Máquina"]) == ["e47"]
    assert query_keywords("maquina E47") == ["maquina", "e47"]
    for stop, expected in (([], {"maquina parada", "E47 no forno"}), (["Máquina"], {"E47 no forno"})):
        m = make(settings, SearchConfig(vector_weight=0.0, stopwords=stop))
        seed(m, "maquina parada", 0.1)
        seed(m, "E47 no forno", 0.1)
        for i in range(4):
            seed(m, f"other note {i}", 0.9)
        assert set(names(m.search("maquina E47", top_k=10, **ALICE))) == expected


def test_max_df():
    """C10 (AC 8): more than `max_df` of the searchable rows, not at most."""
    six = [row(f"pump {i}") for i in range(6)]
    five = [row(f"pump {i}") for i in range(5)]
    assert keyword_rank(six, ["pump"], searchable=10, max_df=0.5, limit=20) == ([], [])
    ranked, kept = keyword_rank(five, ["pump"], searchable=10, max_df=0.5, limit=20)
    assert len(ranked) == 5 and kept == ["pump"]
    ten = [row(f"pump {i}") for i in range(10)]
    assert len(keyword_rank(ten, ["pump"], searchable=10, max_df=1.0, limit=20)[0]) == 10


def test_no_keyword_left(settings):
    """C11 (AC 9): only stopwords, a word no row holds, or a word every row holds: the vector result, `vector`."""
    m = make(settings)
    for i, text in enumerate(["the pump is noisy", "pump belt replaced", "pump seal leaks", "pump oil changed"]):
        seed(m, text, 0.9 - i * 0.1)
    for query in ("le client préfère le café", "de la do the", "pump"):
        on = m.search(query, hybrid=True, top_k=10, **ALICE)
        off = m.search(query, hybrid=False, top_k=10, **ALICE)
        assert [r["id"] for r in on] == [r["id"] for r in off], query
        assert {r["matched_by"] for r in on} == {"vector"}, query
        assert [{k: v for k, v in r.items() if k != "matched_by"} for r in on] == off, query
    # several memory types: with no keyword left, merged by similarity, as with hybrid off (Verifier finding 1)
    for text, sim in (("fact one", 0.95), ("fact two", 0.85)):
        seed(m, text, sim)
    with m.store.unit_of_work() as uow:
        for text, sim in (("episode one", 0.75), ("episode two", 0.65)):
            uow.memories.add_memory(
                type="episode", schema_version=1, scope="user", workspace_id=WS, user_id="alice", content=text,
                payload={"content": text}, entities=[], embedding=unit(sim), evidence=[], status="active",
                verified=True, created_by="test")
    for query in ("de la do the", "zzzz"):
        on = m.search(query, types=["fact", "episode"], hybrid=True, top_k=4, **ALICE)
        off = m.search(query, types=["fact", "episode"], hybrid=False, top_k=4, **ALICE)
        assert names(on) == names(off), query
        assert [r["similarity"] for r in on] == sorted((r["similarity"] for r in on), reverse=True), query
        assert {r["type"] for r in on} == {"fact", "episode"} or len(on) == 4, query


def test_idf_rank():
    """C12 (AC 10): one rare keyword outranks two common ones; a repeated keyword counts once; ties by similarity."""
    rows = [
        row("rare alpha", 0.1),                        # alpha df 1
        row("beta gamma", 0.2),                        # beta df 4, gamma df 4: two common keywords
        row("beta beta beta beta", 0.3),               # beta repeated: counts once
        row("beta x", 0.4), row("gamma y", 0.5), row("gamma beta", 0.05), row("gamma z", 0.6),
    ]
    ranked, kept = keyword_rank(rows, ["alpha", "beta", "gamma"], searchable=10, max_df=0.5, limit=20)
    assert kept == ["alpha", "beta", "gamma"]
    order = names(ranked)
    idf = lambda df: math.log(1 + (10 - df + 0.5) / (df + 0.5))  # noqa: E731
    assert idf(1) > 2 * idf(4)  # the case is a real test of the formula, not of the keyword count
    assert order[:3] == ["rare alpha", "beta gamma", "gamma beta"]
    assert order[3:] == ["gamma z", "gamma y", "beta x", "beta beta beta beta"]


# --- S3 ------------------------------------------------------------------------------------------------------------

def test_hybrid_off_is_today(settings, monkeypatch):
    """C13 (AC 11): no `matched_by`, and the keyword leg is never asked."""
    def boom(*a, **kw):
        raise AssertionError("keyword_search called with hybrid off")

    monkeypatch.setattr(_Tx, "keyword_search", boom)  # the in-memory unit of work's methods
    for m, kw in ((make(settings, SearchConfig(hybrid=False)), {}), (make(settings), {"hybrid": False})):
        seed(m, "fault E47 on the unit", 0.5)
        seed(m, "fault E74 on the unit", 0.9)
        rows = m.search("E47", top_k=5, **kw, **ALICE)
        assert names(rows) == ["fault E74 on the unit", "fault E47 on the unit"]
        assert all("matched_by" not in r for r in rows)


def test_per_call_override(settings):
    """C15 (AC 12)."""
    off = make(settings, SearchConfig(hybrid=False))
    on = make(settings)
    for m in (off, on):
        seed(m, "fault E47 on the unit", 0.5)
        seed(m, "fault E74 on the unit", 0.9)
    assert names(off.search("E47", hybrid=True, **ALICE))[0] == "fault E47 on the unit"
    assert all("matched_by" in r for r in off.search("E47", hybrid=True, **ALICE))
    assert names(on.search("E47", hybrid=False, **ALICE))[0] == "fault E74 on the unit"
    assert all("matched_by" not in r for r in on.search("E47", hybrid=False, **ALICE))
    calls = on.embedder.calls
    with pytest.raises(ValidationError, match="hybrid"):
        on.search("E47", hybrid="yes", **ALICE)
    assert on.embedder.calls == calls


def test_search_defaults(settings, tmp_path):
    """C16 (AC 13): in code and from a yaml with no `search:` block."""
    (tmp_path / "memhub.yaml").write_text(yaml.safe_dump({"project": "p"}))
    for search in (settings.search, resolve(tmp_path / "memhub.yaml").config.search):
        assert (search.hybrid, search.vector_weight, search.keyword_weight, search.keyword_max_df, search.stopwords) \
            == (True, 1.0, 1.0, 0.5, [])
    m = make(settings)
    seed(m, "fault E47 on the unit", 0.5)
    seed(m, "fault E74 on the unit", 0.9)
    rows = m.search("E47", **ALICE)
    assert names(rows)[0] == "fault E47 on the unit" and rows[0]["matched_by"] == "both"


def test_fuse_weights():
    """C17 (AC 14)."""
    vector = [row(f"v{i}", 1.0 - i * 0.1) for i in range(1, 6)]   # v1 .. v5, similarity 0.9 .. 0.5
    keyword = [row("v5", 0.5), row("k only", 0.85)]
    assert names(fuse(vector, keyword, vector_weight=1.0, keyword_weight=1.0, k=10)) == \
        ["v5", "v1", "k only", "v2", "v3", "v4"]  # k only (1/62) ties v2 (1/62): similarity 0.85 > 0.8
    assert names(fuse(vector, keyword, vector_weight=1.0, keyword_weight=0.0, k=10)) == ["v1", "v2", "v3", "v4", "v5"]
    assert names(fuse(vector, keyword, vector_weight=0.0, keyword_weight=1.0, k=10)) == ["v5", "k only"]
    assert names(fuse(vector, keyword, vector_weight=1.0, keyword_weight=1.0, k=2)) == ["v5", "v1"]
    # a heavier keyword weight lifts a keyword-only row above the vector leader
    assert names(fuse(vector, keyword, vector_weight=1.0, keyword_weight=3.0, k=3)) == ["v5", "k only", "v1"]


def test_vector_weight_on_a_type_without_keywords(settings):
    """C24 (AC 14): merging types, a type whose keywords `keyword_max_df` dropped still weighs its rows by
    `vector_weight` (Verifier finding 5): 0.2 / (60 + 1) for its first row, below a fused row of 1 / (60 + 1)."""
    m = make(settings, SearchConfig(vector_weight=0.2))
    seed(m, "fact E47 fault", 0.3)
    for i in range(5):
        seed(m, f"fact other {i}", 0.9 - i * 0.01)
    with m.store.unit_of_work() as uow:
        for i in range(2):  # every episode names E47: in the episode search the keyword is dropped (2/2 > 0.5)
            text = f"episode E47 {i}"
            uow.memories.add_memory(
                type="episode", schema_version=1, scope="user", workspace_id=WS, user_id="alice", content=text,
                payload={"content": text}, entities=[], embedding=unit(0.95 - i * 0.01), evidence=[],
                status="active", verified=True, created_by="test")
    rows = m.search("E47", types=["fact", "episode"], top_k=3, **ALICE)
    # fact E47 fault: 1/61 from the keyword leg (+ 0.2/66 from the vector leg, ranked as deep as the keyword leg);
    # episode E47 0: 0.2/61, similarity 0.95 (vector only); fact other 0: 0.2/61, similarity 0.9. With 1/61 for the
    # vector-only rows, an episode would tie the fused row.
    assert names(rows) == ["fact E47 fault", "episode E47 0", "fact other 0"]
    assert [r["matched_by"] for r in rows] == ["both", "vector", "vector"]


def test_tiers_keep_their_order(settings):
    """C18 (AC 15): a keyword-only row of tier 2 never ranks above tier 1; each tier's hits are its subject's."""
    m = make(settings)
    prd1, prd2, prd3 = ({"type": "machine", "id": f"PRD_{i}"} for i in (1, 2, 3))
    seed(m, "PRD_1 belt check", 0.6, [prd1])
    seed(m, "PRD_1 oil level", 0.5, [prd1])
    seed(m, "PRD_2 fault E47", 0.05, [prd2])
    seed(m, "PRD_2 noise", 0.95, [prd2])
    seed(m, "PRD_3 fault E47", 0.9, [prd3])
    rows = m.search("E47", filters={"entities": [prd1, prd2]}, top_k=10, **ALICE)
    assert names(rows)[:2] == ["PRD_1 belt check", "PRD_1 oil level"]
    assert set(names(rows)[2:]) == {"PRD_2 fault E47", "PRD_2 noise"}
    assert names(rows)[2] == "PRD_2 fault E47"  # tier 2 fused: both legs beat the vector leader
    assert {r["matched_on"] for r in rows[:2]} == {"machine:PRD_1"}


def test_matched_by_and_similarity(settings):
    """C19 (AC 16); the toolkit and MCP views carry it, the middleware's text block does not change."""
    m = make(settings)
    seed(m, "fault E47 near", 0.9)        # both legs
    seed(m, "a vector neighbour", 0.8)    # vector only
    for i in range(25):
        seed(m, f"filler {i}", 0.7 - i * 0.01)
    seed(m, "fault E47 far", 0.02)        # keyword only: outside the vector leg's k
    rows = {r["content"]: r for r in m.search("E47", top_k=5, **ALICE)}
    assert rows["fault E47 near"]["matched_by"] == "both"
    assert rows["a vector neighbour"]["matched_by"] == "vector"
    assert rows["fault E47 far"]["matched_by"] == "keyword"
    for content, sim in (("fault E47 near", 0.9), ("a vector neighbour", 0.8), ("fault E47 far", 0.02)):
        assert abs(rows[content]["similarity"] - sim) < 1e-6, content
    # a boost stays in `similarity`, as with hybrid off (AC 16 as restated, AD-060)
    boosted = make(settings)
    from memhub.storage.rules import area_link
    with boosted.store.unit_of_work() as uow:
        area = uow.memories.add_memory(
            type="area", schema_version=1, scope="user", workspace_id=WS, user_id="alice", content="Rooftop",
            payload={"key": "rooftop", "title": "Rooftop"}, entities=[], embedding=unit(0.2), evidence=[],
            status="active", verified=True, created_by="test")
    seed(boosted, "fault E47 in the area", 0.5, links=[area_link(area["memory_id"])])
    seed(boosted, "unrelated", 0.4)
    from memhub.storage.rules import Actor
    alice = Actor(id="alice", roles=[])
    on = boosted.service.search(alice, "E47", workspace_id=WS, user_id="alice", area="rooftop")
    off = boosted.service.search(alice, "E47", workspace_id=WS, user_id="alice", area="rooftop", hybrid=False)
    assert on[0]["content"] == "fault E47 in the area" and on[0]["matched_by"] == "both"
    assert abs(on[0]["similarity"] - 0.55) < 1e-6 and on[0]["similarity"] == off[0]["similarity"]
    kit = m.tools(workspace_id=WS, user_id="alice", run_id="r")
    view = {r["content"]: r for r in kit.search_memory("E47")}
    assert view["fault E47 far"]["matched_by"] == "keyword"
    assert json.loads(kit.call("search_memory", {"query": "E47"}))[0]["matched_by"] in ("both", "keyword", "vector")
    from memhub.middleware import _format
    assert "matched_by" not in _format({**rows["fault E47 far"], "evidence": []}) and "keyword" not in \
        _format({**rows["fault E47 far"], "evidence": []})


@pytest.mark.parametrize("search, key", [
    ({"vector_weight": -1}, "search.vector_weight"),
    ({"keyword_weight": -0.1}, "search.keyword_weight"),
    ({"keyword_max_df": 0}, "search.keyword_max_df"),
    ({"keyword_max_df": 1.5}, "search.keyword_max_df"),
    ({"vector_weight": 0, "keyword_weight": 0}, "vector_weight"),
    ({"stopwords": "x"}, "search.stopwords"),
])
def test_invalid_search_config(tmp_path, search, key):
    """C20 (AC 17)."""
    path = tmp_path / "memhub.yaml"
    path.write_text(yaml.safe_dump({"project": "p", "search": search}))
    with pytest.raises(ConfigError, match=key.replace(".", r"\.")) as exc:
        resolve(path)
    if key == "vector_weight":
        assert "keyword_weight" in str(exc.value)


def test_config_show_lists_search_keys(tmp_path, monkeypatch):
    """C21 (AC 18)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memhub.yaml").write_text(yaml.safe_dump({"project": "p", "search": {"stopwords": ["maquina"]}}))
    result = CliRunner().invoke(cli.app, ["config", "show", "--all"], env={})
    assert result.exit_code == 0, result.output
    shown = {}
    for line in result.output.splitlines():
        parts = line.split("  ")
        if len(parts) >= 3:
            shown[parts[0]] = parts[1]
    assert shown["search.hybrid"] == "true" or shown["search.hybrid"] == "True"
    assert float(shown["search.vector_weight"]) == 1.0 and float(shown["search.keyword_weight"]) == 1.0
    assert float(shown["search.keyword_max_df"]) == 0.5
    assert "maquina" in shown["search.stopwords"]


# --- S4 ------------------------------------------------------------------------------------------------------------

def test_bench_codes():
    """C22 (AC 19): no database; one line per code-style query with the expected memory's rank, on and off."""
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "bench_search.py"), "--codes"], cwd=ROOT,
                         capture_output=True, text=True, env={"PYTHONPATH": f"{ROOT / 'src'}:{ROOT}", "PATH": "/usr/bin"})
    assert out.returncode == 0, out.stderr
    lines = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
    assert len(lines) >= 5
    for line in lines:
        assert set(line) >= {"query", "expected", "rank_hybrid", "rank_vector"}
    assert all(line["rank_hybrid"] == 1 for line in lines)


def test_hybrid_docs():
    """C22 (AC 20)."""
    filters = (ROOT / "docs" / "filters.md").read_text()
    assert "## Hybrid search" in filters
    section = filters.split("## Hybrid search", 1)[1]
    for key in ("search.hybrid", "search.vector_weight", "search.keyword_weight", "search.keyword_max_df",
                "search.stopwords"):
        assert key in section, key
    for word in ("Italian", "French", "English", "Portuguese", "`true`", "`1.0`", "`0.5`"):
        assert word in section, word
    assert "FTS5" in (ROOT / "docs" / "sqlite.md").read_text()
