-- The SQLite ledger: the Postgres tables of `storage/postgres.py`, column for column.
-- uuids and timestamps are TEXT (ISO-8601 UTC, microseconds), JSON columns are TEXT, booleans INTEGER,
-- the embedding a BLOB of little-endian float32. `{prefix}` is the project prefix. One statement per `;`.

CREATE TABLE IF NOT EXISTS {prefix}_memory (
  id TEXT PRIMARY KEY,
  memory_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  type TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  scope TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  user_id TEXT,
  status TEXT NOT NULL,
  verified INTEGER NOT NULL,
  content TEXT NOT NULL,
  payload TEXT NOT NULL,
  entities TEXT NOT NULL DEFAULT '[]',
  embedding BLOB,
  evidence TEXT NOT NULL DEFAULT '[]',
  seen_count INTEGER NOT NULL DEFAULT 1,
  score REAL,
  conflicts_with TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  reviewed_by TEXT,
  reviewed_at TEXT,
  review_note TEXT,
  observed_at TEXT NOT NULL,
  valid_from TEXT,
  valid_until TEXT,
  durability TEXT,
  assertion TEXT NOT NULL DEFAULT 'stated',
  links TEXT NOT NULL DEFAULT '[]',
  run_id TEXT,
  agent_id TEXT,
  UNIQUE (memory_id, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS {prefix}_memory_active_uidx
  ON {prefix}_memory (memory_id) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS {prefix}_memory_scope_idx
  ON {prefix}_memory (workspace_id, scope, status, type);

CREATE INDEX IF NOT EXISTS {prefix}_memory_user_idx
  ON {prefix}_memory (user_id) WHERE user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS {prefix}_memory_key_uidx
  ON {prefix}_memory (workspace_id, scope, COALESCE(user_id, ''), type, json_extract(payload, '$.key'))
  WHERE status = 'active' AND json_extract(payload, '$.key') IS NOT NULL;

CREATE TABLE IF NOT EXISTS {prefix}_memory_runs (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  source TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  workspace_id TEXT,
  user_id TEXT,
  first_message_id TEXT NOT NULL,
  last_message_id TEXT NOT NULL,
  last_message_at TEXT NOT NULL,
  final_pass INTEGER NOT NULL DEFAULT 0,
  signals TEXT NOT NULL DEFAULT '[]',
  dropped TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  candidates_proposed INTEGER NOT NULL DEFAULT 0,
  created INTEGER NOT NULL DEFAULT 0,
  merged INTEGER NOT NULL DEFAULT 0,
  tokens_in INTEGER,
  tokens_out INTEGER,
  cost_usd REAL,
  processed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS {prefix}_runs_thread_idx
  ON {prefix}_memory_runs (source, thread_id, processed_at DESC);

CREATE TABLE IF NOT EXISTS {prefix}_memhub_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)
