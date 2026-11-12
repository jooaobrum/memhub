"""Numbered schema migrations, one SQL file per version per backend.

    storage/migrations/postgres/0002_run_and_agent_ids.sql
    storage/migrations/sqlite/0002_run_and_agent_ids.sql

`NNNN` is the schema version the file produces; version 1 is the v0.1.0 schema. A ledger stores its version in the
meta row `schema_version`; a ledger with tables and no such row is version 1. `{prefix}` in a file is the table
prefix; statements are split on `;`, and lines starting with `--` are comments. Every backend has a file for every
version (comments only when it has nothing to change); the in-memory backend has no SQL and only records the
version.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from memhub.storage.rules import SchemaOutdated

SCHEMA_VERSION = 2
SCHEMA_KEY = "schema_version"
HERE = Path(__file__).parent
SQL_BACKENDS = ("postgres", "sqlite")
_FILE = re.compile(r"^(\d{4})_(\w+)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str  # "0002_run_and_agent_ids": the file name without `.sql`, the same on every backend
    path: Path | None  # None: a backend with no SQL (in memory)

    def statements(self, prefix: str) -> list[str]:
        if self.path is None:
            return []
        lines = [l for l in self.path.read_text().splitlines() if not l.lstrip().startswith("--")]
        return [s.strip() for s in "\n".join(lines).replace("{prefix}", prefix).split(";") if s.strip()]


def files(backend: str) -> dict[int, Path]:
    """The migration files of a SQL backend, by version."""
    out: dict[int, Path] = {}
    for path in sorted((HERE / backend).glob("*.sql")):
        match = _FILE.match(path.name)
        if match is None:
            raise ValueError(f"{path} is not named NNNN_name.sql")
        version = int(match.group(1))
        if version in out:
            raise ValueError(f"two migrations produce schema version {version} for {backend}")
        out[version] = path
    return out


def migrations(backend: str) -> list[Migration]:
    """Every migration of `backend`, oldest first. The names are the Postgres files' names."""
    names = {v: p.stem for v, p in files("postgres").items()}
    paths = files(backend) if backend in SQL_BACKENDS else {}
    return [Migration(v, names[v], paths.get(v)) for v in sorted(names)]


def pending(backend: str, current: int | None) -> list[Migration]:
    """The migrations a ledger at version `current` still needs, in order. `None` (no ledger) has none to take."""
    if current is None:
        raise ValueError("there is no ledger to migrate: run `memhub init` (or `Memory.init()`) first")
    return [m for m in migrations(backend) if m.version > current]


def check(version: int) -> None:
    """Raise `SchemaOutdated` unless the ledger is at this memhub's schema version."""
    if version < SCHEMA_VERSION:
        raise SchemaOutdated(
            f"the ledger is at schema version {version} and this memhub needs {SCHEMA_VERSION}: run `memhub migrate`")
    if version > SCHEMA_VERSION:
        raise SchemaOutdated(
            f"the ledger is at schema version {version}, newer than this memhub ({SCHEMA_VERSION}): upgrade memhub")
