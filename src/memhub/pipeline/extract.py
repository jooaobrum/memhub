"""One structured LLM call per segment."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, create_model, field_validator

from memhub.config import Durability, Settings
from memhub.pipeline.llm import invoke_structured
from memhub.pipeline.segment import Segment
from memhub.types import Episode, TypeRegistry


class Evidence(BaseModel):
    message_id: str
    quote: str
    claim_source: Literal["user", "tool", "assistant"]


class NewArea(BaseModel):
    title: str
    description: str = ""


class AreaRef(BaseModel):
    """One area of a candidate: an existing area's key, or a proposed new area."""

    existing: str | None = None
    new: NewArea | None = None


class ClosedAreaRef(BaseModel):
    """`AreaRef` when the project does not let the model propose areas (`areas.open: false`)."""

    existing: str


class Candidate(BaseModel):
    type: str
    scope: Literal["user", "workspace"]
    fields: dict[str, Any]
    evidence: list[Evidence]
    utility: int = Field(ge=1, le=5)
    applies_generally: bool
    durability: Durability = "ongoing"
    assertion: Literal["stated", "inferred"]
    areas: list[AreaRef] = Field(default_factory=list)  # one to three; only for a type configured with `area`
    valid_from: datetime | None = None  # only when the text gives one; a bare date is read as UTC midnight
    valid_until: datetime | None = None

    @field_validator("valid_from", "valid_until")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return v.replace(tzinfo=timezone.utc) if v is not None and v.tzinfo is None else v


class Extraction(BaseModel):
    candidates: list[Candidate]


@dataclass
class ExtractResult:
    candidates: list[Candidate] = field(default_factory=list)
    ok: bool = True
    tokens_in: int = 0
    tokens_out: int = 0


def extractable_types(settings: Settings, registry: TypeRegistry, final_pass: bool) -> list[str]:
    names = [n for n, cfg in settings.types.items() if cfg.extract and n != "area"]
    if final_pass:
        names = [n for n in names if issubclass(registry.latest(n), Episode)]
    return names


def _fields_class(name: str, registry: TypeRegistry, keyed: dict[str, dict[str, str]], open_keys: frozenset = frozenset()) -> type:
    """The type's registry class; for a keyed type, `key` is narrowed to an enum of the configured keys
    (unless the type's keys are only suggestions: then `key` stays a free, optional short name)."""
    cls = registry.latest(name)
    if name not in keyed or name in open_keys:
        return cls
    return create_model(f"{cls.__name__}Keyed", __base__=cls, key=(Literal[tuple(keyed[name])], ...))  # type: ignore[valid-type]


def make_schema(
    type_names: list[str], registry: TypeRegistry, keyed: dict[str, dict[str, str]] | None = None,
    area_types: dict[str, str] | None = None, areas_open: bool = True, open_keys: frozenset = frozenset(),
) -> type[Extraction]:
    """`Extraction` whose candidates are a discriminated union (on `type`) of the enabled types,
    each carrying its own registry class as `fields`, so the LLM gets per-type structure.
    `keyed` maps a keyed type to its allowed keys, offered to the model as an enum. A type in `area_types`
    carries `areas` (no `new` entry when the areas are closed); the others carry none."""
    keyed, area_types = keyed or {}, area_types or {}
    ref = AreaRef if areas_open else ClosedAreaRef
    variants = [
        create_model(
            f"Candidate_{name}", __base__=Candidate,
            type=(Literal[name], ...), fields=(_fields_class(name, registry, keyed, open_keys), ...),  # type: ignore[valid-type]
            **({"areas": (list[ref], Field(default_factory=list))} if name in area_types else {}),  # type: ignore[valid-type]
        )
        for name in type_names
    ]
    cand = variants[0] if len(variants) == 1 else Annotated[Union[tuple(variants)], Field(discriminator="type")]  # type: ignore[valid-type]
    return create_model("Extraction", __base__=Extraction, candidates=(list[cand], ...))  # type: ignore[valid-type]


_TERM_SECTION = (
    "`term` (the shared glossary of this domain): propose one when (a) a message defines or confirms it, for example "
    "\"CNH significa carteira nacional de habilitação\" or \"CNH é a mesma coisa que carteira de motorista\": set "
    "`confirmed_by_user` true, and every alias MUST then appear in the quote, copied verbatim; or (b) the user uses a "
    "domain term (an acronym, an administrative or local word) whose meaning and equivalents YOU are at least 90% sure "
    "of: leave `confirmed_by_user` false and set `confidence` (0 to 1) honestly; below 0.9 nothing is stored. Only "
    "domain vocabulary that a newcomer would need explained, never everyday words, and never because two words merely "
    "appear together. "
    "`term` is the short form, `expansion` what it stands for (optional), `aliases` the interchangeable names, "
    "`related` other terms of the same topic (optional). "
    "`content` = \"TERM (expansion): alias1, alias2\". A `term` has scope `workspace`; every other type has "
    "scope `user`.\n"
)


def _prompt(
    names: list[str], registry: TypeRegistry, max_candidates: int, final_pass: bool, entity_types: list[str],
    instructions: str, keyed: dict[str, dict[str, str]] | None = None,
    area_types: dict[str, str] | None = None, areas: list[tuple[str, str, str]] | None = None, areas_open: bool = True,
    open_keys: frozenset = frozenset(),
) -> str:
    keyed, area_types = keyed or {}, area_types or {}
    slots = "".join(
        (
            f"`{n}` suggested keys (a guide to what characterises this kind; set `key` to one of them when the memory is "
            f"exactly that, otherwise use a short snake_case name of your own or leave `key` empty; never force a memory "
            f"into a key it does not state):\n"
            if n in open_keys
            else f"`{n}` keys (one memory per key; anything that matches no key is NOT stored as `{n}`):\n"
        )
        + "".join(f"  - {k}: {d}\n" for k, d in keyed[n].items())
        for n in names if n in keyed
    ) + _area_section(names, area_types, areas or [], areas_open) + (_TERM_SECTION if "term" in names else "")
    types = "\n".join(f"- {n}: fields {', '.join(registry.latest(n).model_fields)}" for n in names)
    scope = "the whole finished conversation" if final_pass else "a slice of a conversation"
    entities = (
        f"`entities` may only use these types: {', '.join(entity_types)}."
        if entity_types
        else "No entity types are configured: `entities` MUST be an empty list."
    )
    return (
        f"You extract long-term memories for an assistant from {scope}.\n"
        f"{instructions.strip()}\n"
        f"Return at most {max_candidates} candidates, and never two candidates for the same idea "
        "(pick the single best type).\n"
        "Evidence: for each candidate give the message_id (the label in brackets, like #3), a SHORT quote "
        "(at most one sentence, under 200 characters) copied VERBATIM and contiguous from that single "
        "message, and who made the claim (user, tool or assistant) - it must match the role shown in the "
        "transcript.\n"
        "Time: each message shows its date, like [#2 2026-03-10]. Resolve relative time against the date of "
        "the message that says it, and NEVER write relative time (next year, in 3 months, I'm 18) into "
        "`content`. Example: \"I'm 18\" -> \"Born around 2008 (18 on 2026-03-10)\", stable. "
        "Set `valid_until` (ISO date) only when the text gives an end: \"next year\" -> the end of next "
        "year, \"for 3 months\" -> 3 months after the message. Set `valid_from` only when the text gives a "
        "start. Otherwise leave both null.\n"
        "A stated intention (\"I'll apply next year\", \"I'm moving to Paris\") is not stored; store only the "
        "durable fact behind it (a `fact`, or a profile attribute) when there is one.\n"
        "Propose an `episode` only when the text gives a situation, an action and an outcome; never invent a "
        "missing part.\n"
        "`durability`: stable = identity, documents held, past events; ongoing = studies, work, where they "
        "live; temporary = a current need or errand.\n"
        "`assertion`: stated = the user said it; inferred = the content goes beyond what the user literally "
        "said (a deduction, a guess from context).\n"
        + (
            "FINAL PASS: candidates MUST all be `episode` (the type field is always \"episode\"); never emit fact or "
            "preference here (earlier passes already captured them). Emit an episode ONLY for something that happened "
            "to the user with a situation, an action and an outcome that the USER told; this is NOT a summary of the "
            "conversation and never lists what the user asked about. Most conversations have none: return `candidates` [].\n"
            if final_pass else ""
        )
        + f"{entities}\n"
        f"`type` must be exactly one of: {', '.join(names)}.\n"
        f"Memory types and their `fields`:\n{types}\n{slots}"
    )

def _area_section(
    names: list[str], area_types: dict[str, str], areas: list[tuple[str, str, str]], areas_open: bool
) -> str:
    """The areas a memory may belong to, and what `areas` must hold for each type that takes them."""
    typed = [n for n in names if n in area_types]
    if not typed:
        return ""
    lines = "".join(f"  - {key}: {title} - {description}\n" for key, title, description in areas)
    rules = "".join(
        f"`{n}`: `areas` = 1 to 3 areas that fit; "
        + (
            "a candidate that fits no area MUST NOT be emitted as a `fact` (a statement about who the user is, such as age or identity, is a `profile`).\n" if area_types[n] == "required"
            else "may be left empty when none fits.\n"
        )
        for n in typed
    )
    return (
        "Areas (topics a memory is about); each entry of `areas` is {\"existing\": key}"
        + (
            " or {\"new\": {\"title\": ..., \"description\": ...}}. Reuse an existing area whenever one fits; "
            "propose a new one only when none does.\n" if areas_open
            else ". Only the listed areas may be used; never invent one.\n"
        )
        + f"{lines}{rules}"
    )


def seed_areas(settings: Settings) -> list[tuple[str, str, str]]:
    return [(a.key, a.title, a.description) for a in settings.areas.seeds]


def _labels(segment: Segment) -> dict[str, str]:
    """Short labels (#1, #2, ...) shown to the model instead of real message ids: real ids are
    often long opaque strings that a small model miscopies, which would make every quote ungrounded."""
    return {f"#{i}": m.message_id for i, m in enumerate(segment.messages, 1)}

def _resolve_id(raw: str, labels: dict[str, str], real_ids: set[str]) -> str:
    """A real message id wins; otherwise accept the label however the model wrote it (#3, 3, [#3], m3)."""
    if raw in real_ids:
        return raw
    key = (raw.strip().strip("[]").split() or [""])[0].lstrip("#mM")  # the model may echo "#3 2026-03-10"
    return labels.get(f"#{key}", raw)

def _relocate(ev: Evidence, segment: Segment) -> None:
    """A small model often quotes the right sentence but cites the neighbouring message (a user turn and the
    assistant turn of the same line are adjacent). When the quote is in no message it cites, point the evidence at the
    message that does say it, provided it was written by whoever the claim is attributed to."""
    quote = " ".join(ev.quote.split()).lower()
    said = lambda m: quote and quote in " ".join(m.content.split()).lower()
    cited = next((m for m in segment.messages if m.message_id == ev.message_id), None)
    if cited is not None and said(cited):
        return
    hits = [m for m in segment.messages if said(m) and ev.claim_source in (m.role, "tool")]
    if len(hits) == 1:
        ev.message_id = hits[0].message_id


def _transcript(segment: Segment, assistant_chars: int | None = None) -> str:
    """The assistant's long answers add little (a claim needs the user's words) and make a cheap model's JSON
    more likely to break, so they may be shown cut; a quote from the shown part is still verbatim in the message."""
    def shown(m) -> str:
        return m.content[:assistant_chars] + "..." if assistant_chars and m.role == "assistant" and len(m.content) > assistant_chars else m.content
    return "\n".join(f"[#{i} {m.timestamp.date().isoformat()}] {m.role}: {shown(m)}" for i, m in enumerate(segment.messages, 1))

_TERM_INSTRUCTIONS = (
    "Propose glossary terms only: an administrative acronym, an official document or procedure name, or a local word "
    "that a newcomer to this domain would need explained, used or defined in the conversation. Give `confidence` (0 to 1, "
    "honest; below 0.9 nothing is kept) when YOU know its meaning; set `confirmed_by_user` true only when the user "
    "themself defines or confirms it (then every alias must appear in the quote). Never an everyday word, a place, a "
    "person or a brand. Most conversations have no term: return an empty list then."
)


def _sample(
    extractor: Any, segment: Segment, names: list[str], instructions: str, max_c: int, passes: int, *,
    settings: Settings, registry: TypeRegistry, areas,
) -> tuple[ExtractResult, list[Candidate]]:
    """`passes` independent structured calls for the given types; their candidates united, a repeat counted once."""
    messages = [
        ("system", _prompt(names, registry, max_c, segment.is_final_pass, settings.entity_types, instructions,
                           settings.keyed_types(), settings.area_types(),
                           areas if areas is not None else seed_areas(settings), settings.areas.open,
                           frozenset(settings.open_key_types()))),
        ("human", _transcript(segment, settings.ingestion.assistant_chars)),
    ]
    schema = make_schema(
        names, registry, settings.keyed_types(), settings.area_types(), settings.areas.open,
        frozenset(settings.open_key_types()),
    )
    result, candidates, seen = ExtractResult(ok=False), [], set()
    for _ in range(passes):
        parsed, tokens_in, tokens_out = invoke_structured(
            extractor, schema, messages, retries=settings.extraction.retries, what=f"extraction (thread {segment.thread_id})",
        )
        result.tokens_in += tokens_in
        result.tokens_out += tokens_out
        result.ok = result.ok or parsed is not None
        for c in parsed.candidates if parsed is not None else []:
            key = (c.type, " ".join(str(getattr(c.fields, "content", c.fields)).split()).lower(), tuple(e.quote for e in c.evidence))
            if key not in seen:
                seen.add(key)
                candidates.append(c)
    return result, candidates


def _extract_halves(extractor: Any, segment: Segment, failed: ExtractResult, *, settings, registry, areas) -> ExtractResult:
    """A slice the model keeps failing on is cut at a user turn and each half is extracted on its own: smaller
    prompts, smaller outputs. A slice with a single user turn cannot be cut and stays an extract_error."""
    users = [i for i, m in enumerate(segment.messages) if m.role == "user"]
    if len(users) < 2:
        return failed
    cut = users[len(users) // 2]
    out = ExtractResult(tokens_in=failed.tokens_in, tokens_out=failed.tokens_out)
    for part in (segment.messages[:cut], segment.messages[cut:]):
        sub = extract(extractor, Segment(segment.thread_id, segment.workspace_id, segment.user_id, part, segment.is_final_pass),
                      settings=settings, registry=registry, areas=areas)
        out.tokens_in += sub.tokens_in
        out.tokens_out += sub.tokens_out
        out.ok = out.ok and sub.ok
        out.candidates += sub.candidates
    out.ok = out.ok or bool(out.candidates)  # one half read is better than nothing; the other half's loss is logged
    return out


def extract(
    extractor: Any, segment: Segment, *, settings: Settings, registry: TypeRegistry,
    areas: list[tuple[str, str, str]] | None = None,
) -> ExtractResult:
    """Model/network exceptions propagate (the segment is retried next run); invalid
    structured output yields ok=False and no candidates. Cost is not computed (no pricing table in v1)."""
    names = extractable_types(settings, registry, segment.is_final_pass)
    if not names:
        return ExtractResult()
    # Glossary terms get a call of their own: in the main call they lose the few candidate slots to the user's memories.
    main = [n for n in names if n != "term"] if len(names) > 1 else names
    max_c, passes = settings.ingestion.max_candidates, max(1, settings.extraction.passes)
    kw = dict(settings=settings, registry=registry, areas=areas)
    # A model that breaks the schema (a truncated JSON, an unlisted type) is sampled again; if it keeps failing the
    # slice is cut in two, and if that fails too it is recorded as extract_error, not retried forever. With several
    # `passes` the samples are independent: what one misses another often finds, and a repeat is merged later.
    result, candidates = _sample(extractor, segment, main, settings.extraction.instructions, max_c, passes, **kw)
    if not result.ok:
        return _extract_halves(extractor, segment, result, **kw)
    if main != names:
        terms, found = _sample(extractor, segment, ["term"], _TERM_INSTRUCTIONS, 2, 1, **kw)
        result.tokens_in += terms.tokens_in
        result.tokens_out += terms.tokens_out
        candidates = candidates[: max_c * passes] + found[:2]  # a failed glossary call loses only the terms
    else:
        candidates = candidates[: max_c * passes]
    labels = _labels(segment)
    real_ids = {m.message_id for m in segment.messages}
    result.candidates = candidates
    for cand in result.candidates:
        for ev in cand.evidence:
            ev.message_id = _resolve_id(ev.message_id, labels, real_ids)  # unknown stays unknown -> ungrounded
            _relocate(ev, segment)
    return result
