"""Stand-ins for an app's own `build()` function, named by `connect:` in the tests."""
from __future__ import annotations

from typing import Any

STATE: dict[str, Any] = {}  # what `build()` returns, set by each test
CALLS: list[tuple] = []
NOT_CALLABLE = 42


def reset(**entries: Any) -> None:
    STATE.clear()
    STATE.update(entries)
    CALLS.clear()


def build(*args: Any) -> Any:
    CALLS.append(args)
    if "return" in STATE:
        return STATE["return"]
    return {k: v for k, v in STATE.items()}
