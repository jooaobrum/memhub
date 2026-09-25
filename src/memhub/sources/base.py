"""The adapter contract every source implements."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Protocol


@dataclass
class Interaction:
    """One normalised message, whatever the source's native shape."""

    thread_id: str
    user_id: str
    workspace_id: str
    message_id: str
    role: str  # "user" | "assistant" | "tool"
    content: str
    timestamp: datetime
    trace_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(Protocol):
    """A source yields `Interaction`s, plus a per-line/trace skip count for the run summary."""

    def read(self) -> Iterator[Interaction]: ...
