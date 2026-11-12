"""Memory types: the built-in typed memories, and the registry that lets a
project add its own.

Every stored memory's ``payload`` validates against one of these classes at
its own ``schema_version``. The registry key is ``(type_name, schema_version)``
so an old row is never silently reinterpreted by a newer class.
"""
from __future__ import annotations

import importlib
import re
from collections import defaultdict
from typing import Any, ClassVar

from pydantic import BaseModel, Field, create_model
from pydantic.json_schema import SkipJsonSchema


class EntityRef(BaseModel):
    """A reference to an external entity, e.g. {"type": "machine", "id": "M-12"}.

    ``type`` must be one of the project's configured ``entity_types``; that is
    checked by the caller (config/service), not by this model, since the
    registry has no config to check against.
    """

    type: str
    id: str


class MemoryBase(BaseModel):
    """Base class every memory type extends.

    ``content`` is the one human-readable statement that gets embedded and
    shown to the agent. Subclasses add whatever structured fields they need.
    """

    content: str
    entities: list[EntityRef] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    # Overridable by subclasses; bumped by hand when a type's shape changes.
    schema_version: ClassVar[int] = 1


class Fact(MemoryBase):
    """An open-ended, standalone statement about the world or the user."""


class Preference(MemoryBase):
    """One active row per (user, key) — e.g. key="language", key="answer_style"."""

    key: str


class Profile(MemoryBase):
    """Who the person is (identity, role, constraints, goals), as free text. The extractor never sees a `key`; a project
    may still mark the type `keyed`, and then the schema narrows `key` to the configured list."""

    key: SkipJsonSchema[str | None] = None


class Episode(MemoryBase):
    """A past situation -> actions -> outcome."""

    situation: str
    actions: str
    outcome: str


class Case(MemoryBase):
    """A solved problem, saved on request (`/remember save this case`): what was seen, why, what was done, how it ended.
    A project with other fields writes its own class; `content_template` in the type's config composes `content`."""

    symptom: str
    root_cause: str
    action: str
    outcome: str


class Skill(MemoryBase):
    """A procedural memory: a named, described instruction body."""

    name: str
    description: str
    body: str


class Area(MemoryBase):
    """The header of an area page; created by the pipeline, never proposed as an extraction candidate.

    `key` names the area (a seed's config key, or a slug of a proposed title); `summary` is derived text
    written by the summarizer, and `summary_rows` fingerprints the linked rows it was written from."""

    title: str
    description: str = ""
    summary: str = ""
    proposed: bool = False
    key: str | None = None
    summary_label: str = ""
    summary_rows: str = ""


class Term(MemoryBase):
    """One entry of the shared glossary: `term` (optionally spelled out as `expansion`), its interchangeable
    `aliases`, and `related` terms of the same topic. `content` is written so it embeds:
    "CNH (carteira nacional de habilitação): carteira de motorista, permis de conduire"."""

    term: str
    expansion: str | None = None
    aliases: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)  # the model's own confidence when IT defines the term
    confirmed_by_user: bool = False  # the user defined or confirmed it in their own words: counts as 100%


def term_content(term: str, expansion: str | None, aliases: list[str]) -> str:
    return f"{term}{f' ({expansion})' if expansion else ''}: {', '.join(aliases)}" if aliases else (
        f"{term} ({expansion})" if expansion else term
    )


_SCALARS: dict[str, Any] = {"str": str, "int": int, "float": float, "bool": bool, "list[str]": list[str], "list[int]": list[int]}


def model_from_fields(name: str, fields: dict[str, Any]) -> type[MemoryBase]:
    """A memory type declared in the yaml, no Python needed: `{equipment: str, fault_code: str, fix: "str?"}`.
    A field is a type name (`str`, `int`, `float`, `bool`, `list[str]`, `list[int]`; a trailing `?` makes it optional) or
    `{type: str, description: "...", default: ...}`; the description is shown to the extractor."""
    spec: dict[str, Any] = {}
    for field_name, raw in fields.items():
        info = raw if isinstance(raw, dict) else {"type": raw}
        type_name = str(info.get("type", "str")).strip()
        optional = type_name.endswith("?")
        base = _SCALARS.get(type_name.rstrip("?"))
        if base is None:
            raise ValueError(f"field {field_name!r} of type {name!r}: unknown type {type_name!r} (use {sorted(_SCALARS)})")
        default = info.get("default", None if optional else ...)
        spec[field_name] = (base | None if optional else base, Field(default, description=info.get("description")))
    return create_model("".join(p.title() for p in name.split("_")), __base__=MemoryBase, **spec)


_PLACEHOLDER = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?::([^?]+))?(\?)?")


def _pieces(template: str) -> list[tuple[bool, str]]:
    """`template` cut into `(optional, text)` pieces: text outside `[...]`, and each `[...]` section. `[[` and `]]` are
    literal brackets; a bracket inside `{...}` (`{tags:list[str]}`) belongs to the placeholder."""
    out: list[tuple[bool, str]] = []
    buf, section, depth, i = "", False, 0, 0
    while i < len(template):
        ch, two = template[i], template[i:i + 2]
        if depth == 0 and two in ("[[", "]]"):
            buf += ch
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
        elif depth == 0 and ch == "[":
            if section:
                raise ValueError(f"template {template!r}: a [section] cannot hold another")
            out.append((False, buf))
            buf, section = "", True
            i += 1
            continue
        elif depth == 0 and ch == "]":
            if not section:
                raise ValueError(f"template {template!r}: ']' closes no section (write ']]' for a literal bracket)")
            out.append((True, buf))
            buf, section = "", False
            i += 1
            continue
        buf += ch
        i += 1
    if section or depth:
        raise ValueError(f"template {template!r}: a {'[section' if section else '{placeholder'} is never closed")
    out.append((False, buf))
    return [(opt, text) for opt, text in out if text or opt]


def parse_template(type_name: str, template: str) -> tuple[dict[str, str], str]:
    """A type written as a template: `"Saw {species} at {location}[ on {seen_on?}][ (count {count:int?})]"`.
    Each placeholder `{name}`, `{name?}`, `{name:type}`, `{name:type?}` declares a field (type default `str`, `?`
    optional); returns the fields and the template with bare `{name}` placeholders, sections kept."""
    fields: dict[str, str] = {}

    def field(m: re.Match[str]) -> str:
        inner = m.group(1)
        if inner.startswith("{"):  # `{{` is a literal brace
            return m.group(0)
        p = _PLACEHOLDER.fullmatch(inner.strip())
        if p is None:
            raise ValueError(f"type {type_name!r}: {{{inner}}} is not a field (write {{name}}, {{name?}} or {{name:int?}})")
        name, kind, optional = p.group(1), (p.group(2) or "str").strip(), p.group(3) or ""
        if kind not in _SCALARS:
            raise ValueError(f"field {name!r} of type {type_name!r}: unknown type {kind!r} (use {', '.join(sorted(_SCALARS))})")
        spec = kind + optional
        if fields.get(name, spec) != spec:
            raise ValueError(f"field {name!r} of type {type_name!r} is declared twice with different types ({fields[name]}, {spec})")
        fields[name] = spec
        return "{" + name + "}"

    _pieces(template)  # refuse an unbalanced template before anything else
    bare = re.sub(r"\{(\{?[^{}]*)\}", field, template)
    return fields, bare


def render_template(template: str, values: dict[str, Any]) -> str:
    """`content` composed from a type's `content_template`. A `[...]` section is left out when one of its placeholders
    has no value (None, "" or an empty list); outside sections a missing value is empty text."""
    from string import Formatter

    filled = defaultdict(str, {k: "" if v is None else v for k, v in values.items()})
    out = []
    for optional, text in _pieces(template):  # `[[` / `]]` come back as single brackets
        if optional and any(name and values.get(name.split(".")[0].split("[")[0]) in (None, "", []) for _, name, _, _ in Formatter().parse(text)):
            continue
        out.append(text.format_map(filled))
    return "".join(out)


BUILTIN_TYPES: dict[str, type[MemoryBase]] = {
    "fact": Fact,
    "preference": Preference,
    "profile": Profile,
    "episode": Episode,
    "case": Case,
    "skill": Skill,
    "area": Area,
    "term": Term,
}

# A built-in type's options when a project turns it on (`fact: builtin`) or gets the starter types; `area` and `term`
# are memhub's own (area pages, the glossary) and come with the starter only.
BUILTIN_OPTIONS: dict[str, dict[str, Any]] = {
    "fact": {"type_prior": 0.6},
    "preference": {"type_prior": 0.8},
    "profile": {"type_prior": 0.8},
    "episode": {"type_prior": 0.4},
    "case": {"type_prior": 0.7, "extract": False, "on_remember": True},
    "skill": {"retrieval": "index_then_load", "extract": False},
    "area": {"extract": False, "scopes": ["user"]},
    "term": {"scopes": ["workspace"]},
}
INTERNAL_TYPES = ("area", "term")
STARTER_TYPES = ("fact", "preference", "profile")


def builtin_spec(name: str) -> dict[str, Any]:
    """The long form of a built-in type: its class and options."""
    return {"class": f"memhub.types:{BUILTIN_TYPES[name].__name__}", **BUILTIN_OPTIONS[name]}


class TypeRegistry:
    """Maps (type_name, schema_version) -> MemoryBase subclass.

    Built-ins are pre-registered at schema_version=1. Project types are added
    with :meth:`register` or :meth:`register_path` (``"module:Class"``, the
    form used in ``memhub.yaml``).
    """

    def __init__(self) -> None:
        self._classes: dict[tuple[str, int], type[MemoryBase]] = {}
        self._latest_version: dict[str, int] = {}
        for name, cls in BUILTIN_TYPES.items():
            self.register(name, cls)

    def register(self, type_name: str, cls: type[MemoryBase], *, schema_version: int | None = None) -> None:
        version = schema_version if schema_version is not None else cls.schema_version
        self._classes[(type_name, version)] = cls
        self._latest_version[type_name] = max(version, self._latest_version.get(type_name, 0))

    def register_path(self, type_name: str, dotted_path: str, *, schema_version: int | None = None) -> None:
        """Register a type given as ``"module.sub:ClassName"``."""
        module_name, _, class_name = dotted_path.partition(":")
        if not class_name:
            raise ValueError(f"expected 'module:Class', got {dotted_path!r}")
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        if not (isinstance(cls, type) and issubclass(cls, MemoryBase)):
            raise TypeError(f"{dotted_path} is not a MemoryBase subclass")
        self.register(type_name, cls, schema_version=schema_version)

    def get(self, type_name: str, schema_version: int) -> type[MemoryBase]:
        try:
            return self._classes[(type_name, schema_version)]
        except KeyError as exc:
            raise KeyError(
                f"no class registered for type={type_name!r} schema_version={schema_version}"
            ) from exc

    def latest_version(self, type_name: str) -> int:
        try:
            return self._latest_version[type_name]
        except KeyError as exc:
            raise KeyError(f"unknown memory type {type_name!r}") from exc

    def latest(self, type_name: str) -> type[MemoryBase]:
        return self.get(type_name, self.latest_version(type_name))

    def __contains__(self, type_name: str) -> bool:
        return type_name in self._latest_version

    def type_names(self) -> list[str]:
        return list(self._latest_version)
