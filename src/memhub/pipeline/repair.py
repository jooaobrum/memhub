"""Give a claim that says "next year" or "soon" an absolute date, instead of losing it."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from memhub.pipeline.extract import Candidate
from memhub.pipeline.ground import _RELATIVE_TIME
from memhub.pipeline.llm import invoke_structured
from memhub.pipeline.segment import Segment


class Rewritten(BaseModel):
    content: str


def needs_repair(c: Candidate) -> bool:
    content = getattr(c.fields, "content", None) if not isinstance(c.fields, dict) else c.fields.get("content")
    return c.type != "episode" and bool(content) and bool(_RELATIVE_TIME.search(content))


def repair_relative_time(judge: Any, c: Candidate, segment: Segment, *, retries: int = 0) -> tuple[bool, int, int]:
    """Rewrite the candidate's `content` in place. (repaired, tokens in, tokens out); the candidate is left as it was
    when the judge's answer is unreadable, and grounding then drops it as `relative_time` as before."""
    ids = {e.message_id for e in c.evidence}
    dates = [m.timestamp.date().isoformat() for m in segment.messages if m.message_id in ids]
    if not dates:
        return False, 0, 0
    old = c.fields["content"] if isinstance(c.fields, dict) else c.fields.content
    prompt = (
        f"Rewrite this statement so that it has no relative time. It was said in a message dated {max(dates)}. Replace "
        "each phrase such as \"next year\", \"soon\", \"recently\", \"in 3 months\" by the absolute year, month or date it "
        "means on that date; when it cannot be resolved to a date, drop the phrase. Change nothing else, keep the "
        f"language and the person.\nStatement: {old}"
    )
    parsed, tin, tout = invoke_structured(judge, Rewritten, [("human", prompt)], retries=retries, what="rewrite")
    new = parsed.content.strip() if parsed else ""
    if not new or new == old or _RELATIVE_TIME.search(new):
        return False, tin, tout
    if isinstance(c.fields, dict):
        c.fields["content"] = new
    else:
        c.fields.content = new
    return True, tin, tout
