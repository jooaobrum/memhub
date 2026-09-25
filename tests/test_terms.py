"""Terms: the shared glossary built from explicit definitions (ticket 32), and its use in search (ticket 33)."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from memhub.config import TypeConfig, load_config
from memhub.service import Actor, MemoryService
from memhub.types import term_content
from tests.fakes import FakeChatModel, extraction
from tests.test_ingest_e2e import NOW, cand, env, line  # noqa: F401  (env is a fixture)

ADMIN = Actor(id="admin-1", roles=["workspace_admin"])


@pytest.fixture
def terms_env(env):
    env.settings.types["term"] = TypeConfig(**{"class": "memhub.types:Term"}, type_prior=0.5, scopes=["workspace"])
    return env


def term_cand(quote="CNH significa carteira de motorista", mid="m1", term="CNH", aliases=("carteira de motorista",),
              expansion=None, related=()):
    fields = {
        "content": term_content(term, expansion, list(aliases)), "term": term, "expansion": expansion,
        "aliases": list(aliases), "related": list(related),
    }
    return cand(type="term", scope="workspace", quote=quote, mid=mid, fields=fields)


def define(env, query, candidate, *, thread="c1", mid="m1", age=timedelta(hours=5), judge=None, **kw):
    env.write(line(thread=thread, mid=mid, query=query, age=age))
    return env.run(FakeChatModel([extraction(candidate), extraction()]), judge=judge, **kw)


def terms(env, **kw):
    return sorted(env.memories(type="term", **kw), key=lambda m: (str(m["memory_id"]), m["version"]))


def test_an_explicit_definition_creates_a_workspace_candidate_that_search_ignores_until_approved(terms_env):
    env = terms_env
    s = define(env, "CNH significa carteira de motorista", term_cand())
    assert s.created == 1
    [t] = terms(env)
    assert (t["scope"], t["status"], t["user_id"], t["verified"]) == ("workspace", "candidate", None, False)
    assert t["payload"]["term"] == "CNH" and t["payload"]["aliases"] == ["carteira de motorista"]
    assert [e["quote"] for e in t["evidence"]] == ["CNH significa carteira de motorista"]
    service = MemoryService(store=env.store, settings=env.settings, registry=env.registry, embeddings=env.embeddings)
    assert service.search(ADMIN, "CNH carteira de motorista", workspace_id="ws") == []
    assert [q["id"] for q in service.queue(ADMIN, workspace_id="ws")] == [t["id"]]
    service.approve(ADMIN, t["id"])
    assert [r["id"] for r in service.search(ADMIN, "CNH carteira de motorista", workspace_id="ws")] == [t["id"]]


def test_an_alias_missing_from_the_quote_is_ungrounded_and_nothing_is_created(terms_env):
    env = terms_env
    bad = term_cand(aliases=("carteira de motorista", "permis de conduire"))
    s = define(env, "CNH significa carteira de motorista", bad)
    assert (s.created, s.dropped_by_reason) == (0, {"ungrounded": 1}) and terms(env) == []


def test_a_term_with_nothing_to_expand_to_is_invalid(terms_env):
    s = define(terms_env, "CNH significa CNH", term_cand(quote="CNH significa CNH", aliases=()))
    assert (s.created, s.dropped_by_reason) == (0, {"invalid_fields": 1})


def test_a_second_definition_adds_its_aliases_as_a_new_version_up_to_the_cap(terms_env):
    env = terms_env
    define(env, "CNH significa carteira de motorista", term_cand())
    service = MemoryService(store=env.store, settings=env.settings, registry=env.registry, embeddings=env.embeddings)
    [first] = terms(env)
    service.approve(ADMIN, first["id"])
    q = "cnh é o mesmo que permis de conduire, licence, driving licence, habilitação, carta"
    aliases = ("permis de conduire", "licence", "driving licence", "habilitação", "carta")
    s = define(env, q, term_cand(quote=q, mid="m2", term="cnh", aliases=aliases), thread="c2", mid="m2", age=timedelta(hours=4))
    assert s.created == 1
    v1, v2 = terms(env)
    assert (v1["memory_id"], v1["version"], v1["status"]) == (v2["memory_id"], 1, "active")
    assert (v2["version"], v2["status"]) == (2, "candidate")  # a workspace change waits for approval
    assert v2["payload"]["aliases"] == ["carteira de motorista", "permis de conduire", "licence", "driving licence", "habilitação"]
    assert v2["payload"]["term"] == "CNH"  # the existing spelling is kept
    assert v2["content"] == term_content("CNH", None, v2["payload"]["aliases"])
    # a third definition while v2 is pending amends v2 instead of inserting a clashing version
    q3 = "CNH é o mesmo que licence, carta"
    define(env, q3, term_cand(quote=q3, mid="m3", aliases=("licence", "carta")), thread="c3", mid="m3", age=timedelta(hours=3))
    assert [(t["version"], t["status"]) for t in terms(env)] == [(1, "active"), (2, "candidate")]
    service.approve(ADMIN, terms(env)[1]["id"])
    assert [(t["version"], t["status"]) for t in terms(env)] == [(1, "superseded"), (2, "active")]


def test_a_definition_that_adds_nothing_only_adds_evidence(terms_env):
    env = terms_env
    define(env, "CNH significa carteira de motorista", term_cand())
    s = define(env, "CNH significa carteira de motorista", term_cand(mid="m2"), thread="c2", mid="m2", age=timedelta(hours=4))
    [t] = terms(env)
    assert (s.merged, t["version"], len(t["evidence"]), t["seen_count"]) == (1, 1, 2, 2)


def test_the_term_cap_drops_the_candidate(terms_env):
    env = terms_env
    env.settings.terms.max_per_workspace = 1
    define(env, "CNH significa carteira de motorista", term_cand())
    q = "CAF significa caisse d'allocations familiales"
    s = define(env, q, term_cand(quote=q, mid="m2", term="CAF", aliases=("caisse d'allocations familiales",)),
               thread="c2", mid="m2", age=timedelta(hours=4))
    assert (s.created, s.dropped_by_reason) == (0, {"term_cap": 1}) and len(terms(env)) == 1


def test_a_type_scoped_to_the_workspace_forces_the_scope(terms_env):
    env = terms_env
    c = term_cand().model_copy(update={"scope": "user"})
    define(env, "CNH significa carteira de motorista", c)
    [t] = terms(env)
    assert t["scope"] == "workspace"


def test_habitantes_config_enables_terms_and_only_terms_are_workspace_scope(monkeypatch):
    monkeypatch.setenv("MEMHUB_DATABASE_URL", "postgresql://memhub:memhub@localhost:5433/memhub")
    for name in ("memhub.yaml", "memhub.example.yaml"):
        settings = load_config(Path(__file__).parent.parent / name)
        assert settings.scopes == ["user", "workspace"] and settings.types["term"].extract is True
        assert {n for n in settings.types if "workspace" in settings.type_scopes(n)} == {"term"}
        assert settings.terms.max_aliases == 5 and settings.terms.max_per_workspace == 300
        assert "term" in settings.build_registry()


# --- search expansion (ticket 33) -----------------------------------------------------------------


class Lookup:
    """text -> vector, with a default for anything not listed."""

    def __init__(self, mapping, default):
        self.mapping, self.default = mapping, default

    def embed_query(self, text):
        return self.mapping.get(text, self.default)


@pytest.fixture
def glossary(terms_env, fake_embeddings):
    env = terms_env
    service = MemoryService(store=env.store, settings=env.settings, registry=env.registry, embeddings=fake_embeddings)
    alice = Actor(id="alice", roles=[])

    def add_term(term="CNH", aliases=("carteira de motorista", "permis de conduire"), related=(), actor=ADMIN):
        return service.add(actor, type="term", scope="workspace", workspace_id="ws", fields={
            "content": term_content(term, None, list(aliases)), "term": term, "aliases": list(aliases),
            "related": list(related),
        })

    def add_fact(text):
        return service.add(alice, type="fact", scope="user", user_id="u1", workspace_id="ws", fields={"content": text})

    def search(query, k=1, **kw):
        return service.search(alice, query, workspace_id="ws", user_id="u1", k=k, type="fact", **kw)

    return service, add_term, add_fact, search


def test_an_active_term_widens_the_query_and_the_result_names_it(glossary):
    service, add_term, add_fact, search = glossary
    add_fact("Quer trocar a carteira de motorista brasileira")
    for text in ("Gosta de pizza com queijo", "Mora em Grenoble desde 2024", "Estuda engenharia de dados"):
        add_fact(text)
    assert search("como trocar a CNH")[0].get("expanded_by") is None  # no glossary yet: nothing changes
    add_term()
    [top] = search("como trocar a CNH")
    assert top["content"] == "Quer trocar a carteira de motorista brasileira"
    assert top["expanded_by"] == ["CNH"]
    exp = service.expansion(ADMIN, "como trocar a CNH", workspace_id="ws")
    assert exp.terms == ["CNH"] and exp.text == "como trocar a CNH carteira de motorista permis de conduire"
    # an alias in the query finds the term too, and adds the term itself
    assert service.expansion(ADMIN, "o permis de conduire", workspace_id="ws").text == "o permis de conduire CNH carteira de motorista"
    # a word that only contains the term is not the term
    assert service.expansion(ADMIN, "CNHs", workspace_id="ws").terms == []


def test_a_candidate_term_expands_nothing(glossary):
    service, add_term, add_fact, search = glossary
    add_term(actor=Actor(id="alice", roles=[]))  # not an admin: it lands as a candidate
    exp = service.expansion(ADMIN, "como trocar a CNH", workspace_id="ws")
    assert (exp.terms, exp.text) == ([], "como trocar a CNH")


def test_related_terms_boost_rank_but_add_no_query_text(terms_env):
    from tests.fakes import vec_at

    env = terms_env
    query = vec_at(1.0)
    text = {
        "CNH carteira carteira de motorista": query,
        "Precisa de visto de estudante": vec_at(0.90),
        "Gosta de pizza com queijo": vec_at(0.92),
        "CNH (carteira): carteira de motorista": vec_at(0.5),
    }
    service = MemoryService(store=env.store, settings=env.settings, registry=env.registry, embeddings=Lookup(text, vec_at(0.1)))
    alice = Actor(id="alice", roles=[])
    for t in ("Precisa de visto de estudante", "Gosta de pizza com queijo"):
        service.add(alice, type="fact", scope="user", user_id="u1", workspace_id="ws", fields={"content": t})
    ranked = lambda: [r["content"] for r in service.search(alice, "CNH", workspace_id="ws", user_id="u1", k=2, type="fact")]
    service.add(ADMIN, type="term", scope="workspace", workspace_id="ws", fields={
        "content": "CNH (carteira): carteira de motorista", "term": "CNH", "expansion": "carteira",
        "aliases": ["carteira de motorista"], "related": []})
    assert ranked() == ["Gosta de pizza com queijo", "Precisa de visto de estudante"]
    [term] = env.memories(type="term")
    service.edit(ADMIN, term["memory_id"], {"related": ["visto"]})
    assert service.expansion(ADMIN, "CNH", workspace_id="ws").text == "CNH carteira carteira de motorista"  # nothing added for `visto`
    assert ranked() == ["Precisa de visto de estudante", "Gosta de pizza com queijo"]


def test_a_workspace_with_no_terms_searches_as_before(terms_env, fake_embeddings):
    env = terms_env
    service = MemoryService(store=env.store, settings=env.settings, registry=env.registry, embeddings=fake_embeddings)
    alice = Actor(id="alice", roles=[])
    service.add(alice, type="fact", scope="user", user_id="u1", workspace_id="ws", fields={"content": "Mora em Lyon"})
    [row] = service.search(alice, "onde mora", workspace_id="ws", user_id="u1")
    assert "expanded_by" not in row
    assert service.expansion(alice, "onde mora", workspace_id="ws").text == "onde mora"


def test_a_term_the_model_defines_needs_90_percent_confidence_unless_the_user_confirmed_it(terms_env):
    env = terms_env
    env.write(line(query="Preciso do titre de séjour"))

    def t(**kw):
        c = term_cand(quote="titre de séjour", term="titre de séjour", aliases=("permis",), expansion="residence permit")
        c.fields.update(kw)
        return c

    # the glossary has its own call (second): the main call has none
    s = env.run(FakeChatModel([extraction(), extraction(t(confidence=0.95), t(confidence=0.6, term="visa", aliases=("vlstd",)))]))
    assert s.created == 1 and s.dropped_by_reason == {"low_confidence": 1}
    s = env.run(FakeChatModel([extraction(), extraction(t(confirmed_by_user=True, term="prefecture", aliases=("préfecture",)))]), reprocess=True)
    assert s.dropped_by_reason == {"ungrounded": 1}
