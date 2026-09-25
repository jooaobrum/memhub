"""Deterministic, network-free fakes shared by every test module.

- `FakeEmbeddings`: hash-based bag-of-words embedding. No network. Similar
  text gets a similar vector (shared words -> shared dimensions), so it is
  good enough for novelty / duplicate / reconcile tests, and identical text
  embeds to similarity 1.0.
- `FakeChatModel`: a stand-in for a LangChain chat model that only needs to
  support `.with_structured_output(schema, include_raw=...)`. Responses are
  supplied up front as a queue; `PARSE_ERROR` simulates the extractor
  returning invalid structured output.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

PARSE_ERROR = object()

DEFAULT_USAGE = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


class FakeEmbeddings:
    def __init__(self, dims: int = 16):
        self.dims = dims
        self.calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return self._vec(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += len(texts)
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        for word in text.lower().split():
            digest = int(hashlib.sha256(word.encode()).hexdigest(), 16)
            idx = digest % self.dims
            sign = 1.0 if (digest // self.dims) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


@dataclass
class FakeAIMessage:
    content: str = ""
    usage_metadata: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_USAGE))
    response_metadata: dict[str, Any] = field(default_factory=dict)


class _StructuredRunnable:
    def __init__(self, model: "FakeChatModel", schema: Any, include_raw: bool):
        self.model = model
        self.schema = schema
        self.include_raw = include_raw

    def invoke(self, *_args: Any, **_kwargs: Any) -> Any:
        self.model.calls += 1
        if self.model.unrelated_by_default and getattr(self.schema, "__name__", "") == "Verdict" and not (
            self.model.queue and getattr(self.model.queue[0], "verdict", None)
        ):  # a reconcile question the test did not script: the statements are about different things
            item = self.schema(verdict="unrelated")
            return {"raw": FakeAIMessage(usage_metadata=self.model._next_usage()), "parsed": item, "parsing_error": None} \
                if self.include_raw else item
        if not self.model.queue:
            raise AssertionError("FakeChatModel queue exhausted: add another canned response")
        item = self.model.queue.pop(0)
        raw = FakeAIMessage(usage_metadata=self.model._next_usage())
        if item is PARSE_ERROR:
            if self.include_raw:
                return {"raw": raw, "parsed": None, "parsing_error": ValueError("fake parse error")}
            raise ValueError("fake parse error")
        if self.include_raw:
            return {"raw": raw, "parsed": item, "parsing_error": None}
        return item


class FakeChatModel:
    """Queue up canned structured-output responses; each `.invoke` pops one."""

    def __init__(self, responses: list[Any] | None = None, usages: list[dict[str, int]] | None = None,
                 unrelated_by_default: bool = False):
        self.unrelated_by_default = unrelated_by_default
        self.queue: list[Any] = list(responses or [])
        self._usages = list(usages) if usages else None
        self.calls = 0

    def _next_usage(self) -> dict[str, int]:
        if self._usages:
            return self._usages.pop(0)
        return dict(DEFAULT_USAGE)

    def with_structured_output(self, schema: Any, *, include_raw: bool = False, **_kwargs: Any):
        return _StructuredRunnable(self, schema, include_raw)

    def invoke(self, *_args: Any, **_kwargs: Any) -> FakeAIMessage:
        self.calls += 1
        return FakeAIMessage(usage_metadata=self._next_usage())


def vec_at(similarity: float, dims: int = 16) -> list[float]:
    """A unit vector whose cosine similarity to `vec_at(1.0)` is `similarity`."""
    vec = [0.0] * dims
    vec[0], vec[1] = similarity, math.sqrt(max(0.0, 1 - similarity**2))
    return vec


class MappedEmbeddings:
    """Embedder with an explicit text -> vector map, for controlling similarity exactly."""

    def __init__(self, mapping: dict[str, list[float]]):
        self.mapping = mapping
        self.calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return self.mapping[text]


class ExplodingModel:
    """A chat model whose structured call raises, like a network failure."""

    def __init__(self):
        self.calls = 0

    def with_structured_output(self, schema: Any, *, include_raw: bool = False, **_kwargs: Any):
        model = self

        class _Runnable:
            def invoke(self, *_a: Any, **_k: Any) -> Any:
                model.calls += 1
                raise ConnectionError("boom")

        return _Runnable()

def extraction(*cands: Any) -> Any:
    from memhub.pipeline.extract import Extraction

    return Extraction(candidates=list(cands))


class LookupEmbeddings:
    """text -> vector from a map, with a default vector for any other text (area titles and the like)."""

    def __init__(self, mapping: dict[str, list[float]], default: list[float]):
        self.mapping, self.default = mapping, default
        self.calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return self.mapping.get(text, self.default)


class ScriptedJudge:
    """The judge model of a scenario: queued `Verdict`s for the reconcile question (each call is counted and an
    unexpected one fails the test), and a canned answer for the area summaries."""

    def __init__(self, verdicts: list[Any] | None = None):
        self.queue = list(verdicts or [])
        self.asked = 0

    def with_structured_output(self, schema: Any, *, include_raw: bool = False, **_kwargs: Any):
        judge = self

        class _Runnable:
            def invoke(self, *_a: Any, **_k: Any) -> Any:
                raw = FakeAIMessage()
                if schema.__name__ == "Summary":
                    return {"raw": raw, "parsed": schema(summary="A summary."), "parsing_error": None}
                judge.asked += 1
                if not judge.queue:
                    raise AssertionError("the judge was called but no verdict was expected")
                return {"raw": raw, "parsed": judge.queue.pop(0), "parsing_error": None}

        return _Runnable()
