# memhub on SQLite

memhub runs on one local file, with nothing to install beyond memhub itself: no Docker, no Postgres.
The tables, the review flow and the search rules are the ones Postgres has; the same contract test suite runs
on both.

```bash
pip install "memhub[openai,sqlite] @ git+https://github.com/jooaobrum/memhub.git"   # not `pip install memhub`: that PyPI name is another project
memhub init --db sqlite:///memhub.db --embeddings openai:text-embedding-3-small --chat openai:gpt-4.1-mini
memhub ingest --source <name>         # a source from `sources:` in memhub.yaml, or claude_code
memhub queue
memhub search "what does the user prefer?"
```

```python
import sqlite3
from memhub import Memory

m = Memory(db="sqlite:///memhub.db", llm=chat_model, embedder=embeddings)
m.init()
me = m.scope(workspace_id="acme", user_id="anna", run_id="run-1")
me.add("Prefers answers in Portuguese")
me.search("language")
```

## What `db` can be

| `db` | Connection |
| --- | --- |
| `"sqlite:///memhub.db"` | a file relative to the working directory, opened for each transaction and closed after |
| `"sqlite:////var/data/memhub.db"` | an absolute path (four slashes) |
| `"sqlite:///:memory:"` | one in-memory database for the life of the `Memory`; gone with the process |
| a `sqlite3.Connection` | yours: memhub borrows it, never closes it and keeps its `row_factory` |
| a callable returning a `sqlite3.Connection` | called for each transaction; memhub closes what it returned |

Every memhub transaction starts with `BEGIN IMMEDIATE`, so a second writer waits (up to 5 s on a connection
memhub opened) for the first to commit. On your own connection, when you already have a transaction open,
memhub works in a savepoint inside it: your `commit()` or `rollback()` decides.

Keep the database file out of folders a sync client manages (Google Drive, Dropbox, iCloud, OneDrive): the client
can take the file mid-write and SQLite then fails with `attempt to write a readonly database`. `~/.memhub/` is a
good place.

## Vector search

| | Search runs | When |
| --- | --- | --- |
| `sqlite-vec` | in SQL, `vec_distance_cosine` over the stored vectors | `memhub[sqlite]` is installed and this Python's `sqlite3` can load extensions |
| `python` | in Python, cosine over the owner's rows | otherwise |

Both return the same rows in the same order. Without `memhub[sqlite]` the Python fallback is used; it loads
the candidate rows of the workspace and user for each search, which is fine up to about 50k rows per owner.
Past that, install `memhub[sqlite]` or move to Postgres.

`memhub doctor` and `Memory(...).store.vector_path` say which one is in use. `MEMHUB_SQLITE_VEC=0` forces the
fallback; `MEMHUB_SQLITE_VEC=1` requires sqlite-vec and fails at startup when it cannot load.

## What SQLite cannot do

A missing capability raises `NotImplementedError` naming it and the fix:

| You ask for | You get |
| --- | --- |
| several ingest workers on one thread (`thread_lock`) | `parallel_ingest is not available on sqlite: run one ingest worker or use Postgres` |
| `database_schema` in the config | `schemas is not available on sqlite: remove database_schema from the config, or use Postgres` |

`memhub ingest` itself works: with one worker there is nothing to lock.

## Storage

Uuids and timestamps are text (ISO-8601, UTC), JSON columns are text, embeddings are little-endian float32
blobs. The file can be read with any SQLite tool. Changing the embedding model is `memhub reembed`, as on
Postgres.
