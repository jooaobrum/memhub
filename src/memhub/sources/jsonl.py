"""Field-mapped JSONL source, plus the optional feedback ("signals") file."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from memhub.config import SourceConfig
from memhub.sources.base import Interaction


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _json_lines(path: Path) -> Iterator[dict | None]:
    """Yield each line's object, or None for a malformed line."""
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                yield None
                continue
            yield obj if isinstance(obj, dict) else None


def _timestamp(value) -> datetime:
    """An ISO string (JSONL) or a datetime (a database column)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return _parse_ts(str(value))


def rows_to_interactions(rows: Iterator[dict | None], cfg: SourceConfig, workspace_id: str, counter) -> Iterator[Interaction]:
    """One row per turn (a user message and its answer) -> up to two Interactions. `counter.skipped` counts bad rows.
    `fields.workspace_id` (optional) names the column that holds the row's tenant; else the project's default."""
    f = cfg.fields
    for obj in rows:
        try:
            thread_id, message_id = obj.get(f["thread_id"]), obj.get(f["message_id"])
            if thread_id is None or message_id is None:
                raise ValueError("missing id")
            timestamp = _timestamp(obj[f["timestamp"]])
        except (AttributeError, KeyError, TypeError, ValueError):
            counter.skipped += 1
            continue
        thread_id, message_id = str(thread_id), str(message_id)
        base = dict(
            thread_id=thread_id,
            user_id=str(obj.get(f.get("user_id", ""), thread_id)),
            workspace_id=str(obj.get(f.get("workspace_id", ""), workspace_id)),
            timestamp=timestamp,
            trace_id=str(obj.get(f.get("trace_id", ""), "")),
            metadata={k: obj[k] for k in f.get("metadata", []) if k in obj},
        )
        for role, key, mid in (
            ("user", "user_content", message_id),
            ("assistant", "assistant_content", f"{message_id}:a"),
        ):
            content = obj.get(f.get(key, ""))
            if content:
                yield Interaction(message_id=mid, role=role, content=str(content), **base)


def signals_from_rows(rows, cfg) -> list[dict]:
    """The feedback rows (`signals.join_on`, `signals.map`) as {thread_id, message_id, kind, detail}."""
    out = []
    for obj in rows:
        if obj is None:
            continue
        ids = {canon: obj.get(col) for canon, col in cfg.join_on.items()}
        for column, mapping in cfg.map.items():
            kind = mapping.get(str(obj.get(column)))
            if kind:
                out.append({
                    "thread_id": None if ids.get("thread_id") is None else str(ids["thread_id"]),
                    "message_id": None if ids.get("message_id") is None else str(ids["message_id"]),
                    "kind": kind,
                    "detail": f"{column}={obj.get(column)}",
                })
    return out


class JSONLSource:
    def __init__(self, source_cfg: SourceConfig, *, workspace_id: str, base_dir: Path | None = None) -> None:
        self.cfg = source_cfg
        self.workspace_id = workspace_id
        self.base_dir = base_dir or Path.cwd()
        self.skipped = 0

    def read(self) -> Iterator[Interaction]:
        self.skipped = 0
        yield from rows_to_interactions(_json_lines(self.base_dir / self.cfg.path), self.cfg, self.workspace_id, self)

    def signals(self) -> list[dict]:
        cfg = self.cfg.signals
        if not cfg or not cfg.path or not (self.base_dir / cfg.path).exists():
            return []
        return signals_from_rows(_json_lines(self.base_dir / cfg.path), cfg)
