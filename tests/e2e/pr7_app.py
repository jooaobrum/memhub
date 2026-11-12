"""The app side of the PR 7 demo: the function `connect:` points at. The ingest step sets `LLM` (a scripted
model); the MCP server, which needs no chat model, runs with none."""
from __future__ import annotations

import os

from tests.fakes import FakeEmbeddings

LLM = None


def build() -> dict:
    return {"db": os.environ["PR7_DB"], "embedder": FakeEmbeddings(16), "llm": LLM}
