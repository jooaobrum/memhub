"""A relational table (or view) of conversation turns -> Interactions: the project's own database is the source."""
from __future__ import annotations

import os
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from memhub.config import SourceConfig
from memhub.sources.base import Interaction
from memhub.sources.jsonl import rows_to_interactions, signals_from_rows


class SQLSource:
    """`kind: sql`. `dsn_env` names the environment variable holding the connection string of the project's database
    (any Postgres it can reach; the ledger's own database is fine too), `query` is a SELECT returning one row per turn,
    and `fields` maps the columns exactly like the JSONL source (`thread_id`, `message_id`, `timestamp`, `user_content`,
    `assistant_content`, and optionally `user_id`, `workspace_id`, `trace_id`, `metadata`). `signals.query` is an optional
    second SELECT for feedback rows. Only SELECTs run, in a read-only transaction. The whole result is read each run: put
    a `WHERE timestamp > now() - interval '30 days'` in the query for a large table (the watermark makes reruns cheap)."""

    def __init__(self, source_cfg: SourceConfig, *, workspace_id: str) -> None:
        self.cfg = source_cfg
        self.workspace_id = workspace_id
        self.skipped = 0

    def _rows(self, query: str) -> Iterator[dict]:
        dsn = os.environ.get(self.cfg.dsn_env or "") if self.cfg.dsn_env else None
        if not dsn:
            raise ValueError(f"source needs `dsn_env` naming an environment variable with the database URL (got {self.cfg.dsn_env!r})")
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.read_only = True
            with conn.cursor(name="memhub_source") as cur:  # server-side cursor: a big table is streamed
                cur.execute(query)
                yield from cur

    def read(self) -> Iterator[Interaction]:
        self.skipped = 0
        yield from rows_to_interactions(self._rows(self.cfg.query), self.cfg, self.workspace_id, self)

    def signals(self) -> list[dict]:
        cfg = self.cfg.signals
        query = getattr(cfg, "query", None) if cfg else None
        return signals_from_rows(self._rows(query), cfg) if query else []
