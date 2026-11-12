-- A ledger as memhub 0.1.0 (tag `0.1.0`, `MemoryStore.init`) left it: no `run_id` / `agent_id`, no schema version.
-- `{prefix}`, `{model}` and `{embedding}` (a 16-dim vector literal) are filled in by the test. One statement per `;`.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE {prefix}_memory (
  id uuid PRIMARY KEY,
  memory_id uuid NOT NULL,
  version int NOT NULL,
  type text NOT NULL,
  schema_version int NOT NULL,
  scope text NOT NULL,
  workspace_id text NOT NULL,
  user_id text,
  status text NOT NULL,
  verified boolean NOT NULL,
  content text NOT NULL,
  payload jsonb NOT NULL,
  entities jsonb NOT NULL DEFAULT '[]',
  embedding vector(16),
  evidence jsonb NOT NULL DEFAULT '[]',
  seen_count int NOT NULL DEFAULT 1,
  score real,
  conflicts_with uuid,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  reviewed_by text,
  reviewed_at timestamptz,
  review_note text,
  observed_at timestamptz NOT NULL,
  valid_from timestamptz,
  valid_until timestamptz,
  durability text,
  assertion text NOT NULL DEFAULT 'stated',
  links jsonb NOT NULL DEFAULT '[]'::jsonb,
  UNIQUE (memory_id, version)
);
CREATE UNIQUE INDEX {prefix}_memory_active_uidx ON {prefix}_memory (memory_id) WHERE status = 'active';
CREATE INDEX {prefix}_memory_scope_idx ON {prefix}_memory (workspace_id, scope, status, type);
CREATE INDEX {prefix}_memory_user_idx ON {prefix}_memory (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX {prefix}_memory_entities_gin ON {prefix}_memory USING gin (entities);
CREATE UNIQUE INDEX {prefix}_memory_key_uidx ON {prefix}_memory (workspace_id, scope, COALESCE(user_id, ''), type, (payload->>'key'))
  WHERE status = 'active' AND payload->>'key' IS NOT NULL;
CREATE INDEX {prefix}_memory_links_gin ON {prefix}_memory USING gin (links);
CREATE TABLE {prefix}_memory_runs (
  id uuid PRIMARY KEY,
  run_id uuid NOT NULL,
  source text NOT NULL,
  thread_id text NOT NULL,
  workspace_id text,
  user_id text,
  first_message_id text NOT NULL,
  last_message_id text NOT NULL,
  last_message_at timestamptz NOT NULL,
  final_pass boolean NOT NULL DEFAULT false,
  signals jsonb NOT NULL DEFAULT '[]',
  dropped jsonb NOT NULL DEFAULT '[]',
  status text NOT NULL,
  candidates_proposed int NOT NULL DEFAULT 0,
  created int NOT NULL DEFAULT 0,
  merged int NOT NULL DEFAULT 0,
  tokens_in int,
  tokens_out int,
  cost_usd real,
  processed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX {prefix}_runs_thread_idx ON {prefix}_memory_runs (source, thread_id, processed_at DESC);
CREATE TABLE {prefix}_memhub_meta (key text PRIMARY KEY, value jsonb NOT NULL);
INSERT INTO {prefix}_memhub_meta (key, value) VALUES ('embedding', '{"model": "{model}", "dims": 16}');
CREATE INDEX {prefix}_memory_embedding_hnsw ON {prefix}_memory USING hnsw (embedding vector_cosine_ops);
INSERT INTO {prefix}_memory (id, memory_id, version, type, schema_version, scope, workspace_id, user_id, status, verified,
  content, payload, embedding, created_by, observed_at)
VALUES ('6f1d0c9e-0000-4000-8000-000000000001', '6f1d0c9e-0000-4000-8000-000000000002', 1, 'fact', 1, 'user', 'acme', 'anna',
  'active', true, 'Compressor C3 trips when the inlet filter is clogged',
  '{"content": "Compressor C3 trips when the inlet filter is clogged"}', '{embedding}', 'anna', '2025-06-01T09:00:00Z')
