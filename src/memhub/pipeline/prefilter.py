"""No-LLM skip of segments that cannot hold a memory."""
from __future__ import annotations

from memhub.config import IngestionConfig
from memhub.pipeline.segment import Segment


def should_skip(segment: Segment, ingestion: IngestionConfig, signals: list[dict]) -> bool:
    if any(s["kind"] == "correction" for s in signals):
        return False
    if sum(m.role == "user" for m in segment.messages) < ingestion.min_user_turns:
        return True
    skip = ingestion.skip_when
    return bool(skip) and all(
        any(m.metadata.get(key) in values for key, values in skip.items()) for m in segment.messages
    )
