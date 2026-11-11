"""The storage contract on Postgres + pgvector (Docker, as the rest of the Postgres suite)."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from memhub.storage.contract import *  # noqa: F401,F403
from memhub.storage.contract import backend  # noqa: F401
from memhub.storage.postgres import MemoryStore


@pytest.fixture
def make_backend(pg_dsn):
    prefix = f"c{uuid.uuid4().hex[:8]}"
    return lambda schema: MemoryStore(pg_dsn, prefix, schema)


@pytest.fixture
def unique_error():
    return psycopg.errors.UniqueViolation
