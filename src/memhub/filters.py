"""The `filters=` grammar of search and list (#25), parsed and checked in one place.

    {"entities": {"type": "machine", "id": {"prefix": "PRD_"}}}
    {"AND": [{"entities": [a]}, {"entities": [b]}], "areas": {"in": ["bearings"]}, "observed_at": {"gte": "2026-10-01"}}

Top-level keys are ANDed. A top-level `entities` list is "any of", one search tier per item in its order (AD-030);
everything else is one filter tree (`memhub.storage.rules.Filter`). A bad filter raises `ValidationError` before any
database or model call.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from memhub.service import ValidationError
from memhub.storage.rules import (
    AllOf, AnyOf, AreaRef, ContentContains, EntityIs, EntityTypeIn, Filter, NotOf, TimeRange, TypeIn, all_of,
    named_entity_types,
)

FIELDS = ("entities", "entity_type", "types", "areas", "observed_at", "valid_until", "content")
COMBINATORS = ("AND", "OR", "NOT")
_ALLOWED = ", ".join(FIELDS + COMBINATORS)
_ID_OPS = ("in", "prefix", "contains")
_TIME_OPS = ("gte", "lte")

HINT = (
    "optional: narrow the search. Keys (ANDed): entities ({\"type\", \"id\"} or a list of them; id may be "
    "{\"in\": [..]}, {\"prefix\": \"..\"} or {\"contains\": \"..\"}), entity_type, types (a list), areas (an area key or "
    "title, or {\"in\": [..]}), observed_at / valid_until ({\"gte\": \"2026-10-01\", \"lte\": \"2026-10-31\"}), content "
    "({\"contains\": \"..\"}); combine with AND / OR (lists of filters) and NOT (one filter). "
    "Example: {\"entities\": {\"type\": \"machine\", \"id\": {\"prefix\": \"PRD_\"}}, \"areas\": \"bearings\"}"
)


@dataclass
class SearchFilter:
    """A parsed filter. `items`: the top-level `entities`, one tier each; `rest`: every other top-level key but `types`,
    ANDed; `types`: the top-level memory types (searched one type at a time, as before #25); `has_combinator`: `AND`,
    `OR` or `NOT` at the top level."""

    items: list[EntityIs] = field(default_factory=list)
    rest: Filter | None = None
    types: list[str] | None = None
    has_combinator: bool = False

    def tree(self) -> Filter | None:
        """The whole filter but `types`, as one tree (what `get_all` keeps)."""
        return all_of(AnyOf(tuple(self.items)) if len(self.items) > 1 else (self.items[0] if self.items else None),
                      self.rest)

    def named_types(self) -> set[str]:
        """The entity types a positive branch names (the `require_filter` guard, AD-033)."""
        return {i.type for i in self.items} | named_entity_types(self.rest)


def parse(filters: Any, *, declared: list[str], types: list[str] | None = None, widen: bool = False) -> SearchFilter:
    """`filters` checked against the declared entity types; `types` is the `types=` parameter, merged in."""
    if isinstance(filters, str):  # a model's tool call may send the object as JSON text
        try:
            filters = json.loads(filters)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"filters is not valid JSON: {exc}") from None
    if filters is None:
        if widen:
            raise ValidationError('widen=True needs filters={"entities": [...]} to widen from')
        return SearchFilter(types=types or None)
    if not isinstance(filters, dict):
        raise ValidationError(f"filters must be a dict, not {type(filters).__name__}")
    _keys(filters)
    if "types" in filters and types:
        raise ValidationError("types is given both as a parameter and in filters: keep one")
    out = SearchFilter(types=types or None)
    if "types" in filters:
        out.types = list(_types(filters["types"])) or None
    if "entities" in filters:
        out.items = _entities(filters["entities"], declared, top=True)
    out.rest = all_of(*(_field(k, v, declared) for k, v in filters.items() if k not in ("entities", "types")))
    out.has_combinator = any(k in COMBINATORS for k in filters)
    if widen and out.has_combinator:
        raise ValidationError("widen=True takes no AND / OR / NOT at the top level of filters: widen from a top-level "
                              '"entities" and put the other keys beside it (AD-047)')
    if widen and not out.items:
        raise ValidationError('widen=True needs filters={"entities": [...]} to widen from')
    return out


def _keys(node: dict) -> None:
    unknown = [k for k in node if k not in FIELDS + COMBINATORS]
    if unknown:
        raise ValidationError(f"unknown filter {unknown[0]!r} (allowed: {_ALLOWED})")


def _node(node: Any, declared: list[str]) -> Filter:
    """A nested filter dict (inside `AND` / `OR` / `NOT`): its keys ANDed."""
    if not isinstance(node, dict) or not node:
        raise ValidationError(f"a filter inside AND / OR / NOT is a non-empty dict, got {node!r}")
    _keys(node)
    parts = [_field(k, v, declared) for k, v in node.items() if k != "entities"]
    if "entities" in node:
        items = _entities(node["entities"], declared, top=False)
        parts.insert(0, items[0] if len(items) == 1 else AnyOf(tuple(items)))
    return all_of(*parts)  # type: ignore[return-value]


def _field(key: str, value: Any, declared: list[str]) -> Filter:
    if key == "AND" or key == "OR":
        if not isinstance(value, list) or not value:
            raise ValidationError(f"{key} takes a non-empty list of filters, got {value!r}")
        parts = tuple(_node(v, declared) for v in value)
        return AllOf(parts) if key == "AND" else AnyOf(parts)
    if key == "NOT":
        if not isinstance(value, dict):
            raise ValidationError(f"NOT takes one filter (a dict), got {value!r}")
        return NotOf(_node(value, declared))
    if key == "entities":
        items = _entities(value, declared, top=False)
        return items[0] if len(items) == 1 else AnyOf(tuple(items))
    if key == "entity_type":
        names = _one_or_in(key, value)
        for t in names:
            if t not in declared:
                raise ValidationError(f"unknown entity type {t!r} (declared: {declared})")
        return EntityTypeIn(names)
    if key == "types":
        return TypeIn(_types(value))
    if key == "areas":
        return AreaRef(_one_or_in(key, value))
    if key == "content":
        if not isinstance(value, dict) or set(value) != {"contains"}:
            raise ValidationError(f'content takes {{"contains": "<text>"}}, got {value!r}')
        return ContentContains(_text(key, "contains", value["contains"]))
    return _time(key, value)  # observed_at, valid_until


def _text(key: str, op: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{key} {op} takes a non-empty string, got {value!r}")
    return value


def _one_or_in(key: str, value: Any) -> tuple[str, ...]:
    """`"x"` or `{"in": ["x", ...]}`."""
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, dict):
        if set(value) != {"in"}:
            bad = next((k for k in value if k != "in"), "in")
            raise ValidationError(f"{key} takes a value or {{\"in\": [...]}}, not the operator {bad!r}")
        if not isinstance(value["in"], list) or not value["in"]:
            raise ValidationError(f"{key} in takes a non-empty list, got {value['in']!r}")
        return tuple(_text(key, "in", v) for v in value["in"])
    raise ValidationError(f"{key} takes a non-empty string or {{\"in\": [...]}}, got {value!r}")


def _types(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(t, str) and t for t in value):
        raise ValidationError(f"types takes a list of memory type names, got {value!r}")
    return tuple(value)


def _entities(value: Any, declared: list[str], *, top: bool) -> list[EntityIs]:
    """One `{"type", "id"}` item or a list of them. An empty list is allowed at the top level only (no subject filter,
    as before #25)."""
    items = value if isinstance(value, list) else [value]
    if not items and not top:
        raise ValidationError("entities inside AND / OR / NOT takes at least one {\"type\", \"id\"}")
    return [_entity(e, declared) for e in items]


def _entity(e: Any, declared: list[str]) -> EntityIs:
    if not isinstance(e, dict) or set(e) != {"type", "id"} or not isinstance(e["type"], str) or not e["type"]:
        raise ValidationError(f'an entity is {{"type": str, "id": str or {{"in" | "prefix" | "contains": ...}}}}, got {e!r}')
    if e["type"] not in declared:
        raise ValidationError(f"unknown entity type {e['type']!r} in {e!r} (declared: {declared})")
    id_ = e["id"]
    if isinstance(id_, str) and id_:
        return EntityIs(e["type"], "in", (id_,))
    if not isinstance(id_, dict) or len(id_) != 1 or next(iter(id_)) not in _ID_OPS:
        raise ValidationError(f"an entity id is a string or one of {{\"in\" | \"prefix\" | \"contains\": ...}}, got {id_!r}")
    op, arg = next(iter(id_.items()))
    if op == "in":
        if not isinstance(arg, list) or not arg:
            raise ValidationError(f"entity id in takes a non-empty list, got {arg!r}")
        return EntityIs(e["type"], "in", tuple(_text("entity id", "in", a) for a in arg))
    return EntityIs(e["type"], op, (_text("entity id", op, arg),))


def _bound(key: str, op: str, value: Any) -> tuple[datetime, bool]:
    """A bound as an aware datetime, and whether it was a bare date (a whole UTC day, AD-049)."""
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)), False
    if isinstance(value, date):
        return datetime.combine(value, time(), tzinfo=timezone.utc), True
    if isinstance(value, str):
        try:
            if len(value) == 10:
                return datetime.combine(date.fromisoformat(value), time(), tzinfo=timezone.utc), True
            parsed = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)), False
    raise ValidationError(f"{key} {op} takes an ISO 8601 date or datetime, got {value!r}")


def _time(key: str, value: Any) -> TimeRange:
    if not isinstance(value, dict) or not value:
        raise ValidationError(f'{key} takes {{"gte": <date>, "lte": <date>}} (one or both), got {value!r}')
    bad = [k for k in value if k not in _TIME_OPS]
    if bad:
        raise ValidationError(f"unknown operator {bad[0]!r} on {key} (allowed: gte, lte)")
    gte = lt = lte = None
    if "gte" in value:
        gte, _ = _bound(key, "gte", value["gte"])
    if "lte" in value:
        upper, whole_day = _bound(key, "lte", value["lte"])
        if whole_day:
            lt = upper + timedelta(days=1)
        else:
            lte = upper
    return TimeRange(key, gte=gte, lt=lt, lte=lte)
