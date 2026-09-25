"""Implicit signals found in a segment: user corrections (regex first) and tool/agent errors.

Signal dicts are ``{kind, message_id, detail}`` with kind in
correction | feedback_down | feedback_up | error | rephrase. Feedback comes from the
source's signals file; ``rephrase`` is not detected in v1.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ValidationError

from memhub.sources.base import Interaction

_NOT_POLITE = r"(?!\s*(obrigad|valeu|thanks|thank you|problem))"
_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        rf"^\s*(n[ãa]o|no)\s*[,.!:]{_NOT_POLITE}\s*\w+",
        r"\bna verdade\b",
        r"\bactually,",
        r"\b(errado|errada|incorret[oa])\b",
        r"\b(that'?s|that is|this is|you'?re|you are)\s+(wrong|incorrect|not (right|true|correct))\b",
        r"\bvoc[êe]\s+(errou|se enganou)\b",
        r"\b(it'?s|it is|is|are|was|were)\s+not\s+[^,.]{1,40}?,?\s+but\b",
        r"\bn[ãa]o\s+[ée]\s+[^,.]{1,40}?,?\s+(mas|e sim)\b",
    )
]


def is_correction(text: str) -> str | None:
    for pattern in _PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(0).strip()
    return None


def detect_signals(
    messages: Iterable[Interaction], *, llm_check: Callable[[str], bool] | None = None
) -> list[dict]:
    """`llm_check` is an optional cheap-LLM fallback for user turns the regexes miss (off by default)."""
    out = []
    for m in messages:
        if m.role == "user":
            matched = is_correction(m.content)
            if matched:
                out.append({"kind": "correction", "message_id": m.message_id, "detail": matched})
            elif llm_check and llm_check(m.content):
                out.append({"kind": "correction", "message_id": m.message_id, "detail": "llm"})
        elif m.role == "assistant" and m.metadata.get("error"):
            out.append({"kind": "error", "message_id": m.message_id, "detail": str(m.metadata["error"])})
    return out


class _IsCorrection(BaseModel):
    is_correction: bool


def make_llm_check(llm: Any) -> Callable[[str], bool]:
    """Cheap-LLM fallback for `detect_signals`: does this user turn correct the assistant?"""

    def check(text: str) -> bool:
        try:
            out = llm.with_structured_output(_IsCorrection).invoke(
                [("human", f"Does this user message correct or contradict a previous assistant answer? Message: {text}")]
            )
        except (ValidationError, OutputParserException):
            return False  # a malformed reply from the cheap check must not abort an ingest: treat as "no correction"
        return bool(out and out.is_correction)

    return check
