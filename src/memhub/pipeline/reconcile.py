"""Decide what a proposal means next to what is already stored (decision only; route.py writes)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel

from memhub.config import Settings
from memhub.pipeline.ground import Proposal
from memhub.pipeline.llm import invoke_structured
from memhub.pipeline.segment import Segment
from memhub.store import MemoryStore, Row, area_ids as area_link_ids
from memhub.types import Term, term_content


class Verdict(BaseModel):
    verdict: Literal["same", "extends", "updates", "conflicts", "unrelated"]
    content: str | None = None  # `extends`: the two statements as one, in their own words
    about: int | None = None  # which numbered existing statement the verdict is about; empty = the first


@dataclass
class Decision:
    action: Literal["create", "merge", "supersede", "conflict", "amend", "drop"]
    target: Row | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    reason: str | None = None  # `drop`: outdated | inferred
    extends: bool = False  # `supersede` whose new version merges the two statements and keeps both evidences


def _entity_keys(entities) -> set[tuple[str, str]]:
    return {(e["type"], e["id"]) if isinstance(e, dict) else (e.type, e.id) for e in entities}


def _comparable(p: Proposal, row: Row) -> bool:
    mine, theirs = _entity_keys(p.memory.entities), _entity_keys(row["entities"])
    return bool(mine & theirs) or (not mine and not theirs)


def _shares_area(row: Row, area_ids: set[str] | None) -> bool:
    """A candidate with areas is only compared with rows in one of them; a row with no area is compared as before."""
    if area_ids is None:
        return True
    mine = set(area_link_ids(row["links"]))
    return not mine or bool(mine & area_ids)


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()).lower() == " ".join(b.split()).lower()

def _day(when: datetime | None) -> str:
    return when.date().isoformat() if when else "unknown date"


def _candidate_observed_at(p: Proposal, segment: Segment) -> datetime | None:
    """Newest timestamp among the messages the candidate quotes (the same rule route uses for `observed_at`)."""
    ids = {e.message_id for e in p.candidate.evidence}
    return max((m.timestamp for m in (p.context or segment).messages if m.message_id in ids), default=None)


# Words a merged statement may add to the two it joins (the judge may only use their own words).
_CONNECTORS = {"and", "also", "too", "e", "et", "y", "tambem", "também", "com", "with", "avec", "con", "a", "de", "of", "the", "who", "is", "e"}


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _only_own_words(merged: str, a: str, b: str) -> bool:
    return _words(merged) <= _words(a) | _words(b) | _CONNECTORS


def _ids(evidence: list[dict]) -> set[str]:
    return {e["message_id"] for e in evidence if e.get("message_id")}


def _cand_ids(p: Proposal) -> set[str]:
    return {e.message_id for e in p.candidate.evidence}


class _Judged:
    """The judge for one candidate: called at most once, its tokens counted."""

    def __init__(self, judge: Any, p: Proposal, segment: Segment, retries: int = 0) -> None:
        self.judge, self.p, self.segment, self.retries = judge, p, segment, retries
        self.tokens_in = self.tokens_out = 0
        self.calls = 0

    def ask(self, rows: list[Row]) -> tuple[Row, Verdict]:
        """One call for the candidate, whatever the number of rows: the verdict says which of them it is about."""
        assert self.calls == 0, "the judge is asked once per candidate"
        self.calls += 1
        listed = "".join(f"[{i}] ({_day(r['observed_at'])}) {r['content']}\n" for i, r in enumerate(rows, 1))
        prompt = (
            "A new memory statement about a person, and the statements already stored about them, each with the date "
            "it was observed. Pick the ONE stored statement the new one is about (`about`, its number) and say how "
            "they relate: `same` if they say the same thing; `updates` if the new one replaces the old as a later "
            "state of the same thing (a new city, a new age, a plan now done, a diet or habit given up or changed); "
            "`extends` if they are complementary parts of one statement, neither replacing the other (\"lives with a "
            "French husband\" and \"has a daughter\"): then put both in `content` as ONE statement using only the words "
            "of the two statements; `conflicts` if they contradict each other and it is not clear which one holds; "
            "`unrelated` if the new statement is about none of them (then leave `about` empty). Different topics "
            "are `unrelated`, even when both are about the same person.\n"
            f"Stored:\n{listed}"
            f"New ({_day(_candidate_observed_at(self.p, self.segment))}): {self.p.memory.content}"
        )
        parsed, self.tokens_in, self.tokens_out = invoke_structured(
            self.judge, Verdict, [("human", prompt)], retries=self.retries, what="verdict",
        )
        # An unreadable verdict is treated as a conflict: it lands in review, nothing is merged or overwritten.
        verdict = parsed if parsed is not None else Verdict(verdict="conflicts")
        at = verdict.about if verdict.about and 1 <= verdict.about <= len(rows) else 1
        return rows[at - 1], verdict


def _term_names(term: str, aliases: list[str]) -> set[str]:
    return {term.strip().lower(), *(a.strip().lower() for a in aliases)}


def _reconcile_term(p: Proposal, *, cur, store: MemoryStore, settings: Settings, segment: Segment, embeddings: Any) -> Decision:
    """A definition of a known term (case-insensitive) adds its aliases, up to `terms.max_aliases`; an alias that
    belongs to a different active term is a conflict for the admin. A pending version is amended in place."""
    new: Term = p.memory  # type: ignore[assignment]
    cap = settings.terms.max_aliases
    rows = [
        r for r in store.list_memories(cur, type="term", workspace_id=segment.workspace_id)
        if r["scope"] == "workspace" and r["status"] in ("active", "candidate") and r["conflicts_with"] is None
    ]
    same = [r for r in rows if r["payload"]["term"].strip().lower() == new.term.strip().lower()]
    target = max(same, key=lambda r: r["version"], default=None)
    mine = _term_names(new.term, new.aliases)
    for r in rows:
        if r["status"] == "active" and (target is None or r["memory_id"] != target["memory_id"]) \
                and mine & _term_names(r["payload"]["term"], r["payload"]["aliases"]):
            return Decision("conflict", r)
    old = target["payload"] if target else {"term": new.term, "expansion": None, "aliases": [], "related": []}
    aliases = list(old["aliases"])
    for a in new.aliases:
        if len(aliases) < cap and a.strip().lower() not in _term_names(old["term"], aliases):
            aliases.append(a)
    expansion = old["expansion"] or new.expansion
    related = [*old["related"], *(r for r in new.related if r.strip().lower() not in {x.strip().lower() for x in old["related"]})]
    if target is None and aliases == new.aliases and related == new.related:
        return Decision("create")
    if target is not None and (aliases, expansion, related) == (old["aliases"], old["expansion"], old["related"]):
        return Decision("merge", target)
    p.memory = Term.model_validate({
        **(target["payload"] if target else new.model_dump()), "term": old["term"], "expansion": expansion,
        "aliases": aliases, "related": related, "content": term_content(old["term"], expansion, aliases),
    })
    p.embedding = list(embeddings.embed_query(p.memory.content))
    if target is None:
        return Decision("create")
    return Decision("amend" if target["status"] == "candidate" else "supersede", target)


def _stale(row: Row, now: datetime) -> bool:
    return row["valid_until"] is not None and row["valid_until"] < now


class _Reconciler:
    def __init__(self, p, *, cur, store, settings, segment, judge, embeddings, now) -> None:
        self.p, self.cur, self.store, self.settings, self.segment = p, cur, store, settings, segment
        self.judged = _Judged(judge, p, segment, settings.extraction.retries)
        self.embeddings, self.now = embeddings, now
        self.observed = _candidate_observed_at(p, segment)
        self.area_ids: set[str] | None = None

    def pending_for(self, row: Row) -> Row | None:
        """The candidate already waiting for review against this slot with the same statement."""
        p = self.p
        for pending in self.store.list_memories(
            self.cur, status="candidate", type=p.candidate.type, workspace_id=self.segment.workspace_id,
            user_id=self.segment.user_id if p.scope == "user" else None,
        ):
            if pending["conflicts_with"] == row["memory_id"] and pending["payload"].get("key") == p.memory.key \
                    and _same_text(pending["content"], p.memory.content):
                return pending
        return None

    def conflict(self, row: Row) -> Decision:
        """A candidate for review, unless the same statement is already waiting: then it only gains evidence."""
        pending = self.pending_for(row) if self.settings.is_slot(self.p.candidate.type, self.p.memory) else None
        return Decision("merge", pending) if pending else Decision("conflict", row)

    def against(self, rows: list[Row], *, keyed: bool) -> Decision:
        """The upsert policy for the candidate against the row of its slot (`rows` is that one row), or against the
        nearest rows of its owner and area, of which the judge picks the one it is about."""
        p, s = self.p, self.settings
        row = rows[0]
        if _same_text(row["content"], p.memory.content) or (keyed and _cand_ids(p) <= _ids(row["evidence"])):
            return Decision("merge", row)  # the same value, or this very segment again: evidence only
        if keyed and (pending := self.pending_for(row)) is not None:
            return Decision("merge", pending)  # said before and waiting for review: no new judgement needed
        older = self.observed is not None and self.observed < row["observed_at"]
        stated_over_inferred = p.candidate.assertion == "inferred" and row["assertion"] == "stated"
        mutable = s.is_mutable(p.candidate.type, getattr(p.memory, "key", None))
        if keyed:  # the slot is known: these rules need no judge
            if older:
                return Decision("drop", reason="outdated")
            if row["verified"]:
                return self.conflict(row)
            if stated_over_inferred:
                return Decision("drop", reason="inferred")
            if mutable and _stale(row, self.now):
                return Decision("supersede", row)
        row, verdict = self.judged.ask(rows)
        older = self.observed is not None and self.observed < row["observed_at"]  # the judge may have picked another row
        stated_over_inferred = p.candidate.assertion == "inferred" and row["assertion"] == "stated"
        if verdict.verdict == "extends" and not s.reconcile.extends:  # two rows, unless it is a close paraphrase with more detail
            verdict = Verdict(verdict="same" if (row.get("similarity") or 0) >= s.reconcile.conflict_band[0] else "unrelated")
        if verdict.verdict == "same":
            return Decision("merge", row)
        if verdict.verdict == "unrelated" and not keyed:
            return Decision("create")
        if not keyed:  # the judge said it is about the same thing; now the same rules as for a slot
            if older:
                return Decision("drop", reason="outdated")
            if row["verified"]:
                return self.conflict(row)
            if stated_over_inferred:
                return Decision("drop", reason="inferred")
        if row["status"] != "active" or row["type"] != p.candidate.type:
            return self.conflict(row)  # a pending candidate, or a row of another type: no version to replace, a person decides
        if verdict.verdict == "extends":
            merged = (verdict.content or "").strip()
            if not merged or not _only_own_words(merged, row["content"], p.memory.content) or self.embeddings is None:
                return self.conflict(row)
            p.memory = p.memory.model_copy(update={"content": merged})
            p.embedding = list(self.embeddings.embed_query(merged))
            return Decision("supersede", row, extends=True)
        if verdict.verdict == "updates" and mutable:
            return Decision("supersede", row)
        return self.conflict(row)  # `conflicts`, `unrelated` for a slot, or `updates` of an immutable key

    def decide(self) -> Decision:
        p, s = self.p, self.settings
        owner = self.segment.user_id if p.scope == "user" else None
        if isinstance(p.memory, Term):
            return _reconcile_term(p, cur=self.cur, store=self.store, settings=s, segment=self.segment, embeddings=self.embeddings)
        if s.is_slot(p.candidate.type, p.memory):  # one active row per (owner, type, key): no similarity search
            for row in self.store.list_memories(
                self.cur, status="active", type=p.candidate.type, user_id=owner, workspace_id=self.segment.workspace_id
            ):
                if row["scope"] == p.scope and row["user_id"] == owner and row["payload"].get("key") == p.memory.key:
                    return self.against([row], keyed=True)
            return Decision("create")
        rows = [
            r for t in s.compare_types(p.candidate.type) for r in self.store.top_similar(
                self.cur, type=t, scope=p.scope, workspace_id=self.segment.workspace_id,
                user_id=self.segment.user_id, query_embedding=p.embedding, limit=5 if self.area_ids is None else 25,
            )
        ]
        rows = sorted((r for r in rows if r["similarity"] is not None and _shares_area(r, self.area_ids)),
                      key=lambda r: -r["similarity"])
        for row in rows:  # the same words: nothing new to say
            if _same_text(row["content"], p.memory.content):
                return Decision("merge", row)
        if rows and rows[0]["similarity"] >= s.reconcile.duplicate:
            return Decision("merge", rows[0])
        # A later state of the same thing ("vegan" -> "eats meat again", a new city) embeds far below the duplicate
        # threshold, so the judge sees the nearest rows whatever their similarity, not only those in the band.
        near = [r for r in rows if r["similarity"] >= s.reconcile.compare_floor and _comparable(p, r)]
        if near:
            return self.against(near[: s.reconcile.compare_max], keyed=False)
        return Decision("create")


def reconcile(
    p: Proposal, *, cur, store: MemoryStore, settings: Settings, segment: Segment, judge: Any,
    area_ids: set[str] | None = None, embeddings: Any = None, now: datetime | None = None,
) -> Decision:
    """Decide what a candidate means next to what is stored (the upsert policy of design.md). The judge is asked
    at most once, and never when the two statements are the same after normalising."""
    r = _Reconciler(p, cur=cur, store=store, settings=settings, segment=segment, judge=judge, embeddings=embeddings,
                    now=now or datetime.now(timezone.utc))
    r.area_ids = area_ids
    decision = r.decide()
    decision.tokens_in, decision.tokens_out = r.judged.tokens_in, r.judged.tokens_out
    return decision
