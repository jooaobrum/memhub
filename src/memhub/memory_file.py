"""The project's memory file (`memory.md`): what this project remembers, in one file it owns.

```markdown
---
strict: true
types:
  sighting: "Saw {species} at {location}[ on {seen_on?}]"
areas:
  raptors: hawks, eagles, falcons
---
# What to remember

Only bird species knowledge and the user's sightings.
```

The YAML frontmatter gives `types`, `areas`, `strict`, `entities` and `review` (no other key: connections stay in `memhub.yaml`); the
Markdown body is the extraction prompt (`extraction.instructions`). A file with no frontmatter is all body.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from memhub.config import ConfigError

ALLOWED_KEYS = ("types", "areas", "strict", "entities", "review")


def split(text: str, name: str) -> tuple[dict[str, Any], str]:
    """`(frontmatter, body)`."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ConfigError(f"{name}: the frontmatter opened with --- is never closed") from None
    try:
        front = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{name}: the frontmatter is not valid yaml: {exc}") from exc
    if front is None:
        front = {}
    if not isinstance(front, dict):
        raise ConfigError(f"{name}: the frontmatter must be a mapping of keys, not a {type(front).__name__}")
    return front, "\n".join(lines[end + 1:])


def read_memory_file(path: Path) -> dict[str, Any]:
    """The file as a config layer: `types`, `areas`, `strict`, `entities` as written, `extraction.instructions` = the body (when
    it is not empty)."""
    if not path.is_file():
        raise ConfigError(f"memory file not found: {path.resolve()}")
    front, body = split(path.read_text(), path.name)
    unknown = [k for k in front if k not in ALLOWED_KEYS]
    if unknown:
        raise ConfigError(f"{path.name}: unknown key {unknown[0]!r}; allowed: {', '.join(ALLOWED_KEYS)}")
    layer = dict(front)
    body = body.strip()
    if body:
        layer["extraction"] = {"instructions": body}
    return layer
