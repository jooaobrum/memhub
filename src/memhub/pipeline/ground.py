"""Rule-based checks on candidates: fields, quotes, assistant-only facts, injection."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from pydantic import ValidationError

from memhub import injection
from memhub.config import Settings
from memhub.pipeline.extract import Candidate
from memhub.pipeline.segment import Segment
from memhub.types import MemoryBase, Term, TypeRegistry


# Relative time that means something different when read later. A claim's `content` must carry absolute dates
# (the prompt says so; the cheap extractor ignores it too often to rely on that alone). Unambiguous phrases only:
# "agora" / "atualmente" / "currently" are left alone because they are common in good facts.
_RELATIVE_TIME = re.compile(
    r"\b(ano que vem|(no |o )?pr[óo]ximo (ano|m[êe]s)|(no )?ano seguinte|m[êe]s que vem|semana que vem|recentemente|"
    r"faz pouco tempo|h[áa] pouco tempo|(neste|nesse|este|esse) ano|ano passado|em breve|"
    r"next (year|month|week)|this year|last year|recently|soon|a while ago|in \d+ (days|weeks|months|years))\b",
    re.I,
)


# An "episode" that only retells what the user asked or what was searched (in the language of the conversation):
# a question is never an episode, whatever the model calls it.
_ASKED = re.compile(
    r"\b(perguntou|pediu|solicitou|buscou|procurou|quis saber|questionou|demonstrou interesse|informou-se|"
    r"asked|inquired|requested|searched for|looked for|wanted to know|wondered|was interested|se interessou|"
    r"pedindo ajuda|asking for help|is asking|est[áa] perguntando)\b", re.I,
)


# A need, request or wish is not a fact or a profile row (a preference may well start with "pede": not checked there).
_HABITUAL = re.compile(r"\b([àa]s vezes|sempre|todo|toda|todos|frequentemente|de vez em quando|sometimes|always|often|every)\b", re.I)
_THE_USER = re.compile(r"^\s*(o usu[áa]rio|a usu[áa]ria|o utilizador|a utilizadora|the user)\s+", re.I)  # the voice rule: no "the user" in a memory
_PREF_NEED = re.compile(r"^\s*(procura|busca|precisa|needs?|is looking|looks for)\b", re.I)  # a search is not a preference
_NEED = re.compile(
    r"^\s*(precisa|precisam|quer|querem|procura|procuram|busca|pede|pedem|deseja|gostaria|needs?|wants?|is looking|are looking|"
    r"looks for|asks?|requests?|wishes|considera|considerando|is considering)\b|\b(tem interesse em|interesse em saber|est[áa] em d[úu]vida|em d[úu]vida se|"
    r"is interested in|is wondering|is unsure)\b", re.I,
)
# What the assistant said or did, or how the user introduced themselves, is not something that happened to them.
_ASSISTANT_EPISODE = re.compile(r"\b(assistente|assistant|chatbot|bot)\b|se apresentou|introduced (her|him|them)self", re.I)


@dataclass
class Proposal:
    """A candidate that passed grounding, with its validated memory and (later) embedding/score."""

    candidate: Candidate
    memory: MemoryBase
    scope: str  # the candidate's scope, or settings.scopes[0] when that one is not enabled
    embedding: list[float] | None = None
    score: float | None = None
    questioned: bool = False  # the quote is (part of) a question: it may state nothing about the user
    context: Segment | None = None  # `/remember`: the thread messages the evidence is taken from (beyond the slice)
    explicit: bool = False  # ordered by the user (`/remember`): not weighed against the admission threshold


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _fields(c: Candidate) -> dict:
    return c.fields.model_dump() if hasattr(c.fields, "model_dump") else c.fields

def _check(c: Candidate, segment: Segment, settings: Settings, registry: TypeRegistry, explicit: bool = False):
    """Return (memory, None) or (None, drop reason)."""
    if c.type not in registry:
        return None, "invalid_fields"
    try:
        memory = registry.latest(c.type).model_validate(_fields(c))
    except ValidationError:
        return None, "invalid_fields"
    if not settings.entity_types and memory.entities:
        memory = memory.model_copy(update={"entities": []})  # none are configured: an invented one is noise, not a reason to lose the memory
    if any(e.type not in settings.entity_types for e in memory.entities):
        return None, "invalid_fields"
    by_id = {m.message_id: m for m in segment.messages}
    if not c.evidence:
        return None, "ungrounded"
    good = []
    for e in c.evidence:
        msg = by_id.get(e.message_id)
        quote = _norm(e.quote)
        if msg is None or not quote or quote not in _norm(msg.content):
            continue
        # who made the claim must match who wrote the quoted message
        if e.claim_source in ("user", "assistant") and e.claim_source != msg.role:
            continue
        good.append(e)
    if not good:
        return None, "ungrounded"
    c.evidence = good  # one miscopied quote among several does not cost the memory the others support
    if isinstance(memory, Term):
        # a definition must give something to expand to, and every alias must be in what the person wrote
        if not (memory.expansion or memory.aliases):
            return None, "invalid_fields"
        quotes = " ".join(_norm(e.quote) for e in c.evidence)
        self_defined = not memory.confirmed_by_user and memory.confidence is not None
        if self_defined and memory.confidence < settings.terms.min_confidence:
            return None, "low_confidence"
        if not self_defined and any(_norm(a) not in quotes for a in memory.aliases):
            return None, "ungrounded"  # a user's definition: every alias is in what they wrote
    cfg = settings.types.get(c.type)
    if cfg is not None and cfg.keyed and cfg.strict_keys and getattr(memory, "key", None) not in cfg.keys:
        return None, "unknown_key"
    if c.type == "fact" and all(e.claim_source == "assistant" for e in c.evidence):
        return None, "assistant_only"
    if injection.scan_all([memory.model_dump(), [a.model_dump() for a in c.areas]]):
        return None, "injection"
    area_mode = settings.area_types().get(c.type)
    if area_mode:
        if not settings.areas.open and any(a.new for a in c.areas):
            return None, "invalid_fields"  # proposals are switched off: same as any other invalid output
        if area_mode == "required" and not any(a.existing or a.new for a in c.areas):
            return None, "no_area"
    if c.type == "episode" and (_ASKED.search(memory.content) or _ASSISTANT_EPISODE.search(memory.content)):
        return None, "question_episode"
    if c.type not in ("episode", "case") and _RELATIVE_TIME.search(memory.content):
        return None, "relative_time"  # an Episode is the situation as told; claims must be timeless or dated
    template = settings.types[c.type].content_template if c.type in settings.types else None
    if template:  # the content is composed from the fields, so it embeds and reads the same everywhere
        memory = memory.model_copy(update={"content": template.format_map(defaultdict(str, {k: "" if v is None else v for k, v in memory.model_dump().items()}))})
    if c.type in ("fact", "profile") and len(memory.content.split()) < 2:
        return None, "too_thin"  # a bare word ("Mari") says nothing
    if c.type in ("fact", "profile") and not explicit and _NEED.search(memory.content) and not _HABITUAL.search(memory.content):
        return None, "need_or_request"
    if c.type == "preference" and not explicit and _PREF_NEED.match(memory.content):
        return None, "need_or_request"
    if _THE_USER.match(memory.content):
        rest = _THE_USER.sub("", memory.content, count=1)
        memory = memory.model_copy(update={"content": rest[:1].upper() + rest[1:]})
    if c.type not in settings.keyed_types() and getattr(memory, "key", None) is not None:
        memory = memory.model_copy(update={"key": None})  # the model borrowed a key from another type
    return memory, None


def _questioned(c: Candidate, segment: Segment) -> bool:
    """A quote that contains a question mark, or that is the end of a sentence closed by one."""
    by_id = {m.message_id: m for m in segment.messages}
    for e in c.evidence:
        text, quote = _norm(by_id[e.message_id].content) if e.message_id in by_id else "", _norm(e.quote)
        at = text.find(quote)
        if "?" in quote or (at >= 0 and text[at + len(quote):].lstrip(" .,;!\"'")[:1] == "?"):
            return True
    return False


def ground(
    candidates: list[Candidate], segment: Segment, *, settings: Settings, registry: TypeRegistry, explicit: bool = False
) -> tuple[list[Proposal], list[dict]]:
    kept, dropped = [], []
    for c in candidates:
        memory, reason = _check(c, segment, settings, registry, explicit)
        if reason:
            dropped.append({"candidate": c.model_dump(mode="json"), "reason": reason})
        elif c.assertion == "inferred" and not settings.guardrails.allow_inferred:
            dropped.append({"candidate": c.model_dump(mode="json"), "reason": "inferred"})
        else:
            allowed = settings.type_scopes(c.type)
            scope = c.scope if c.scope in allowed else allowed[0]
            kept.append(Proposal(c, memory, scope, questioned=_questioned(c, segment), explicit=explicit))
    return kept, dropped
