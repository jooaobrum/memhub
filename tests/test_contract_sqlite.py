"""The storage contract on SQLite - no Docker - once per vector path: sqlite-vec (`MEMHUB_SQLITE_VEC=1`, which
fails loudly when the extension cannot load) and the Python cosine fallback (`=0`). Never skipped (AD-017)."""
from __future__ import annotations

import sqlite3

import pytest

from memhub.storage.contract import *  # noqa: F401,F403
from memhub.storage.contract import backend  # noqa: F401
from memhub.storage.sqlite import VEC_ENV, SqliteBackend

VECTOR_PATHS = {"sqlite-vec": "1", "python": "0"}


@pytest.fixture(params=list(VECTOR_PATHS))
def make_backend(request, monkeypatch, tmp_path):
    monkeypatch.setenv(VEC_ENV, VECTOR_PATHS[request.param])
    url = f"sqlite:///{tmp_path / 'contract.db'}"

    def make(schema):
        made = SqliteBackend(url, "contract", schema)
        assert made.vector_path == request.param
        return made

    return make


@pytest.fixture
def unique_error():
    return sqlite3.IntegrityError
