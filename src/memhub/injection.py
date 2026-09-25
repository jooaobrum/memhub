"""A small, mechanical prompt-injection / data-exfiltration pattern scanner.

Used both by `memhub add` (content typed by a human) and by the grounding
step of ingestion (content proposed by the extractor). It is deliberately
simple: a regex allowlist-of-badness, not a classifier.
"""
from __future__ import annotations

import re

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore (all|any)?\s*(previous|prior|above)\s+instructions", re.I),
    re.compile(r"disregard (all|any)?\s*(previous|prior|above)", re.I),
    re.compile(r"system prompt", re.I),
    re.compile(r"you are now (a|an)\b", re.I),
    re.compile(r"new instructions?:", re.I),
    re.compile(r"reveal (your|the) (system )?prompt", re.I),
    re.compile(r"</?(system|assistant|user)>", re.I),
    re.compile(r"\bDAN\b"),
    re.compile(r"send (this|these|the) (data|secret|key|credentials?) to", re.I),
    re.compile(r"exfiltrat", re.I),
]


def scan(text: str) -> str | None:
    """Return the pattern that matched, or None if `text` looks clean."""
    for pattern in _PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    return None


def is_injection(text: str) -> bool:
    return scan(text) is not None


def strings_in(value) -> list[str]:
    """Every string nested anywhere in `value` (dicts, lists, scalars)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings_in(v)]
    return []


def scan_all(value) -> str | None:
    """Like `scan`, over every string field of a memory payload (name, description, body, ...)."""
    for text in strings_in(value):
        matched = scan(text)
        if matched:
            return matched
    return None
