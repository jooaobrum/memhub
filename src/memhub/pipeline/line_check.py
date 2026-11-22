"""A line an agent wrote into its memory file (#41), checked against what the user said.

Like `/remember`, one model call reads the line with the recent messages and returns at most one memory that says
what the line says, with the user's own words as its quote; `ground()` then keeps it only when that quote is found
verbatim in a user message (AD-064, AD-065). The agent's text is never evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from memhub.config import Settings
from memhub.pipeline.extract import AreaRef, _sample
from memhub.pipeline.ground import Proposal, ground
from memhub.pipeline.segment import Segment
from memhub.sources.base import Interaction
from memhub.storage.rules import Row
from memhub.types import TypeRegistry

LINE_INSTRUCTIONS = (
    "An agent asked to keep the line below in long-term memory. Return at most ONE memory that states what the line "
    "states, and only when a USER message in the conversation says it: quote the words of that user message verbatim "
    "(cite its id). Never quote the assistant, never quote the line itself, never add what the line does not say. "
    "If no user message says it, return an empty list. Write in the language of the conversation, without "
    "\"the user\", with absolute dates.\n\nThe line: {line}"
)


def recent(messages: list[dict[str, Any]], settings: Settings, *, workspace_id: str, user_id: str,
           thread_id: str) -> list[Interaction]:
    """The last `ingestion.remember_context` user and assistant messages, as the extractor reads them."""
    now = datetime.now(timezone.utc)
    kept = [m for m in messages if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    window = kept[-settings.ingestion.remember_context:] if settings.ingestion.remember_context > 0 else []
    return [
        Interaction(thread_id=thread_id, user_id=user_id, workspace_id=workspace_id, message_id=f"m{i + 1}",
                    role=m["role"], content=m["content"], timestamp=now, trace_id=thread_id)
        for i, m in enumerate(window)
    ]


def check_line(extractor: Any, line: str, messages: list[Interaction], *, types: list[str], area: Row,
               area_key: str, settings: Settings, registry: TypeRegistry, workspace_id: str, user_id: str,
               thread_id: str) -> tuple[Proposal | None, str | None, Segment]:
    """(the grounded proposal, None), or (None, the drop reason - None when the model found nothing)."""
    segment = Segment(thread_id=thread_id, workspace_id=workspace_id, user_id=user_id, messages=messages)
    if extractor is None:
        raise RuntimeError("checking a memory line needs an llm: pass Memory(llm=...)")
    if not any(m.role == "user" for m in messages):
        return None, None, segment
    offered = [(area_key, area["payload"]["title"], area["payload"].get("description", ""))]
    result, found = _sample(extractor, segment, types, LINE_INSTRUCTIONS.format(line=line), 1, 1,
                            settings=settings, registry=registry, areas=offered)
    if not result.ok:
        raise RuntimeError("the memory check returned no answer")
    found = [c.model_copy(update={"areas": [AreaRef(existing=area_key)]}) for c in found if c.type in types][:1]
    kept, dropped = ground(found, segment, settings=settings, registry=registry, explicit=True)
    if kept:
        return kept[0], None, segment
    return None, (dropped[0]["reason"] if dropped else None), segment
