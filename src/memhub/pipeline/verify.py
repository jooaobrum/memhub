"""A second, cheap opinion on the one kind of candidate the extractor gets wrong most: the episode."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from pydantic import BaseModel

from memhub.pipeline.extract import _transcript
from memhub.pipeline.ground import Proposal
from memhub.pipeline.llm import invoke_structured
from memhub.pipeline.segment import Segment


class EpisodeCheck(BaseModel):
    told_by_user: bool


def _prompt(p: Proposal, segment: Segment) -> str:
    m = p.memory
    return (
        "A candidate memory of type `episode`, and the conversation it was taken from. An episode is valid ONLY when the "
        "USER's own messages state all three parts of something that happened to them: the situation, what they did, "
        "and how it ended. Answer `told_by_user` true only then. False when a part is missing, when it only says who "
        "the user is, where they live or what they do, when it retells what they asked or searched for, or when the "
        "outcome or the action comes from the assistant's answers.\n"
        f"Candidate: situation: {m.situation} | actions: {m.actions} | outcome: {m.outcome}\n"
        f"Conversation:\n{_transcript(segment)}"
    )


def episode_told_by_user(judge: Any, p: Proposal, segment: Segment, *, retries: int = 0) -> tuple[bool, int, int]:
    """(verdict, tokens in, tokens out). An unreadable answer counts as not verified."""
    parsed, tin, tout = invoke_structured(judge, EpisodeCheck, [("human", _prompt(p, segment))], retries=retries, what="episode check")
    return bool(parsed and parsed.told_by_user), tin, tout


# Words that point at someone other than the user.
_OTHER = re.compile(
    r"\b(m[ãa]e|pai|marido|espos[ao]|mulher|filh[oa]s?|irm[ãao]s?|namorad[oa]|amig[oa]s?|cachorr[oa]|gat[oa]|av[óo]|tia|tio|"
    r"primo|prima|sogr[oa]|ela|ele|dela|dele|mother|mom|father|dad|husband|wife|son|daughter|brother|sister|friend|dog|cat|"
    r"she|he|her|his)\b", re.I,
)


def _stems(text: str) -> set[str]:
    """Lower-cased, accent-free word stems (first 5 letters; numbers whole)."""
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return {w[:5] for w in re.findall(r"[a-z0-9]{3,}", plain)}


def worth_checking(p: Proposal) -> bool:
    """Only the claims that look wrong are put to the judge: nothing of the memory is in its quote (copied from the
    prompt's examples, or made up; an abbreviation such as SP is why this is a judge's call and not a rule), or the quote
    mentions someone else while the memory names nobody ("ela tem 68 anos" stored as "Tem 68 anos")."""
    quote = " ".join(e.quote for e in p.candidate.evidence)
    if not _stems(p.memory.content) & _stems(quote):
        return True
    named = _OTHER.search(p.memory.content) or re.search(r"\b[A-ZÀ-Ý][a-zà-ÿ]+", p.memory.content[1:])
    return bool(_OTHER.search(quote)) and not named


class ClaimCheck(BaseModel):
    supported: list[bool]


def unsupported_claims(judge: Any, proposals: list[Proposal], *, retries: int = 0) -> tuple[set[int], int, int]:
    """Indexes of the proposals whose memory its quote does not support. One call for the segment's suspicious claims;
    an unreadable answer keeps them all."""
    if not proposals:
        return set(), 0, 0
    listed = "".join(f"[{i}] quote: {p.candidate.evidence[0].quote} | memory: {p.memory.content}\n" for i, p in enumerate(proposals, 1))
    prompt = (
        "Each item has a quote from a user's message and a memory written from it. Answer `supported` (a list of "
        "booleans, one per item, in order). Be lenient: a memory may rephrase, summarise, translate, expand an "
        "abbreviation, or turn 'next year' or 'yesterday' into an absolute date, and may combine details that appear in "
        "the quote. Give false ONLY in a clear case: (1) the memory names a topic, place, person or thing that the quote "
        "does not mention at all, or (2) the quote says the fact about another person or a pet (a parent, child, partner, "
        "friend) and the memory has no named subject, so it would read as being about the user who wrote the quote.\n" + listed
    )
    parsed, tin, tout = invoke_structured(judge, ClaimCheck, [("human", prompt)], retries=retries, what="claim check")
    if parsed is None or len(parsed.supported) != len(proposals):
        return set(), tin, tout
    return {i for i, ok in enumerate(parsed.supported) if not ok}, tin, tout


class AskedOnly(BaseModel):
    asked_only: list[bool]


def asked_only(judge: Any, proposals: list[Proposal], *, retries: int = 0) -> tuple[set[int], int, int]:
    """Indexes of the proposals taken from a question: the quote only asks or requests, and the memory turns what is
    asked about (a place, a service, a discount) into a statement about the user. One call; unreadable keeps them all."""
    if not proposals:
        return set(), 0, 0
    listed = "".join(f"[{i}] quote: {p.candidate.evidence[0].quote} | memory: {p.memory.content}\n" for i, p in enumerate(proposals, 1))
    prompt = (
        "Each item has a quote from a user's message and a memory written from it. Answer `asked_only` (a list of booleans, "
        "one per item, in order): true when the quote only asks a question or makes a request and the memory is built from "
        "what is being asked about; false when the quote itself states a fact about the user (something they are, have, do, "
        "or decided), even if a question follows it.\n" + listed
    )
    parsed, tin, tout = invoke_structured(judge, AskedOnly, [("human", prompt)], retries=retries, what="question check")
    if parsed is None or len(parsed.asked_only) != len(proposals):
        return set(), tin, tout
    return {i for i, yes in enumerate(parsed.asked_only) if yes}, tin, tout
