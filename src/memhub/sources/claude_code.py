"""Claude Code session transcripts as a source: `~/.claude/projects/<project>/<session>.jsonl`.

    sources:
      claude:                   # optional: `memhub ingest --source claude_code` works with no entry
        kind: claude_code
        path: ~/.claude/projects # the default
        projects: ["*memhub*"]   # project folder globs (default: all)
        user: anna               # default: $MEMHUB_USER, else the OS user

One line of a transcript is one event. A `user` or `assistant` line with text becomes one `Interaction`: the
session is the thread, the project folder the workspace, the line's `uuid` the message id. Tool calls, tool
results, thinking, subagent (sidechain) lines, injected (`isMeta`) text, synthetic assistant lines and the harness
wrappers Claude Code writes as user text are left out. The format is not a public contract, so the reader is
tolerant: a line it cannot read is counted in `skipped` and the next line is read.
"""
from __future__ import annotations

import fnmatch
import getpass
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from memhub.sources.base import Interaction

DEFAULT_PATH = "~/.claude/projects"
WRAPPERS = ("<command-", "<local-command-", "<task-notification>", "<system-reminder>", "<browser_instruction>",
            "[Request interrupted")


def default_user() -> str:
    return os.environ.get("MEMHUB_USER") or getpass.getuser()


def _text(content: Any) -> str:
    """A message's text: a string as it is, else its `text` blocks joined by a blank line."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n\n".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    return ""


def _when(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def interaction(event: dict, *, workspace_id: str, user_id: str) -> Interaction | None:
    """The `Interaction` a transcript line holds, or None when it holds nothing the person or Claude said.
    Raises `KeyError` / `ValueError` / `TypeError` for a line without the fields every message has."""
    kind = event.get("type")
    if kind not in ("user", "assistant") or event.get("isMeta") or event.get("isSidechain"):
        return None
    message = event.get("message") or {}
    if kind == "assistant" and message.get("model") == "<synthetic>":
        return None
    text = _text(message.get("content"))
    if not text or (kind == "user" and text.startswith(WRAPPERS)):
        return None
    session, uuid_, stamp = event["sessionId"], event["uuid"], event["timestamp"]
    if not all(isinstance(v, str) and v for v in (session, uuid_, stamp)):
        raise ValueError("sessionId, uuid and timestamp must be non-empty strings")
    return Interaction(
        thread_id=session, user_id=user_id, workspace_id=workspace_id, message_id=uuid_, role=kind, content=text,
        timestamp=_when(stamp), trace_id=session,
    )


class ClaudeCodeSource:
    def __init__(self, cfg: Any = None, **_: Any) -> None:
        extra = (getattr(cfg, "model_extra", None) or {}) if cfg is not None else {}
        self.path = Path(os.path.expanduser(getattr(cfg, "path", None) or DEFAULT_PATH))
        self.projects = list(extra.get("projects") or ["*"])
        self.user = extra.get("user") or default_user()
        self.skipped = 0
        if not self.path.is_dir():
            raise ValueError(f"no Claude Code transcripts folder at {self.path}")

    def files(self) -> Iterator[tuple[str, Path]]:
        for project in sorted(p for p in self.path.iterdir() if p.is_dir()):
            if any(fnmatch.fnmatch(project.name, g) for g in self.projects):
                for session in sorted(project.glob("*.jsonl")):
                    yield project.name, session

    def read(self) -> Iterator[Interaction]:
        self.skipped = 0
        for project, session in self.files():
            with session.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError("not an object")
                        found = interaction(event, workspace_id=project, user_id=self.user)
                    except (KeyError, ValueError, TypeError, AttributeError):
                        self.skipped += 1
                        continue
                    if found is not None:
                        yield found
