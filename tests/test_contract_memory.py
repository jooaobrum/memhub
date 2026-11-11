"""The storage contract on the in-memory backend - no Docker. Plus what only an in-memory ledger promises:
one unit of work at a time across threads."""
from __future__ import annotations

import threading
import time

import pytest

from memhub.storage.contract import *  # noqa: F401,F403
from memhub.storage.contract import add, backend  # noqa: F401
from memhub.storage.memory import InMemoryBackend
from memhub.storage.rules import Conflict


@pytest.fixture
def make_backend():
    return lambda schema: InMemoryBackend(schema=schema)


@pytest.fixture
def unique_error():
    return Conflict


def _seen_by_b(backend, a_raises: bool) -> tuple[list[str], bool]:
    """A adds a row and holds its unit of work 0.2 s; B starts during that wait. What B finds, and whether it waited."""
    started, found, waited = threading.Event(), [], []

    def a():
        try:
            with backend.unit_of_work() as uow:
                add(uow, "from A")
                started.set()
                time.sleep(0.2)
                if a_raises:
                    raise RuntimeError("A fails")
        except RuntimeError:
            pass

    def b():
        started.wait()
        t0 = time.monotonic()
        with backend.unit_of_work() as uow:
            waited.append(time.monotonic() - t0 >= 0.1)
            found.extend(r["content"] for r in uow.memories.list_memories())

    ta, tb = threading.Thread(target=a), threading.Thread(target=b)
    ta.start(), tb.start()
    ta.join(), tb.join()
    return found, waited[0]


def test_units_of_work_serialize_across_threads(backend):
    assert _seen_by_b(backend, a_raises=False) == (["from A"], True)
    other = InMemoryBackend()
    other.init(embedding_model="contract-embed", dims=16)
    assert _seen_by_b(other, a_raises=True) == ([], True)
