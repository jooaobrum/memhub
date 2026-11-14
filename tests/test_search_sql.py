"""N4 / AD-014: `service.search` on Postgres runs the SQL it ran before the storage ports.

The statements were recorded at 2a58e6c (the last commit before the ports) into tests/data/search_sql_before_ports.txt.
Set MEMHUB_RECORD_SQL=1 to write the file again - only ever on a commit whose search SQL is the reference.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg

from memhub.service import Actor

GOLDEN = Path(__file__).parent / "data" / "search_sql_before_ports.txt"


def _vec(seed: float, dims: int = 16) -> list[float]:
    v = [0.0] * dims
    v[0], v[1] = seed, 1.0 - abs(seed)
    return v


def _record(monkeypatch, prefix: str) -> list[str]:
    seen: list[str] = []
    original = psycopg.Cursor.execute

    def execute(self, query, params=None, *a, **kw):
        text = query if isinstance(query, str) else query.as_string(self)
        seen.append(re.sub(r"\s+", " ", text.replace(prefix, "{p}")).strip())
        return original(self, query, params, *a, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", execute)
    return seen


def test_search_issues_the_sql_recorded_before_the_ports(service, monkeypatch):
    admin = Actor(id="admin-1", roles=["workspace_admin"])
    for text in ("alice prefers tea", "alice lives in Lisbon", "the press runs at night"):
        service.add(admin, type="fact", scope="user", fields={"content": text}, user_id="alice")
    seen = _record(monkeypatch, service.store.prefix)
    rows = service.search(admin, "what does alice drink", user_id="alice", k=2)
    monkeypatch.undo()
    assert len(rows) == 2
    if os.environ.get("MEMHUB_RECORD_SQL") == "1":
        GOLDEN.write_text("\n".join(seen) + "\n")
    assert seen == GOLDEN.read_text().splitlines()


SUBJECTS_GOLDEN = Path(__file__).parent / "data" / "search_sql_subjects_before_filters.txt"


def test_subject_filters_issue_the_sql_recorded_before_the_filter_grammar(store, settings, registry, fake_embeddings,
                                                                          monkeypatch):
    """#25 C18: today's keys with exact ids (`entities` tiers, `entity_type`, `widen`, the `require_filter` guard) run
    the Postgres SQL they ran before the filter grammar. Recorded at 65233c5 (MEMHUB_RECORD_SQL=1 rewrites it)."""
    from memhub.config import EntityConfig
    from memhub.service import MemoryService

    s = settings.model_copy(update={"entity_types": [], "entities": {
        "machine": EntityConfig(), "patient": EntityConfig(require_filter=True)}})
    service = MemoryService(store=store, settings=s, registry=registry, embeddings=fake_embeddings)
    admin = Actor(id="admin-1", roles=["workspace_admin"])
    for text, ent in (("pump P-101 vibrates", "P-101"), ("pump P-102 is quiet", "P-102")):
        service.add(admin, type="fact", scope="user", user_id="alice",
                    fields={"content": text, "entities": [{"type": "machine", "id": ent}]})
    seen = _record(monkeypatch, store.prefix)
    a, b = {"type": "machine", "id": "P-101"}, {"type": "machine", "id": "P-102"}
    service.search(admin, "pump", user_id="alice", k=3, subjects=[a, b])
    service.search(admin, "pump", user_id="alice", k=3, subjects=[a], widen=True)
    service.search(admin, "pump", user_id="alice", k=3, entity_type="machine")
    service.search(admin, "pump", user_id="alice", k=3)
    monkeypatch.undo()
    if os.environ.get("MEMHUB_RECORD_SQL") == "1":
        SUBJECTS_GOLDEN.write_text("\n".join(seen) + "\n")
    assert seen == SUBJECTS_GOLDEN.read_text().splitlines()
