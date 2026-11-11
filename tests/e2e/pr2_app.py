"""The app side of the PR 2 demo: the function `connect:` points at (an app's own `build()`)."""
from __future__ import annotations

import os

from tests.fakes import FakeEmbeddings


def build() -> dict:
    return {"db": os.environ["PR2_DSN"], "embedder": FakeEmbeddings(16)}
