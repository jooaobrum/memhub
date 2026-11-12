-- Schema version 2: the app's run and agent ids on every memory row a call writes (PR 1).
-- Nullable: rows written before stay as they are. `{prefix}` is the project prefix.
ALTER TABLE {prefix}_memory ADD COLUMN IF NOT EXISTS run_id text;
ALTER TABLE {prefix}_memory ADD COLUMN IF NOT EXISTS agent_id text
