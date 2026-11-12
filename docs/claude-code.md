# memhub as memory for Claude Code

Two pieces: a source that turns past Claude Code sessions into memories, and an MCP server that gives every new
session `search_memory` and `propose_memory` over them.

memhub is not on PyPI under that name (`pip install memhub` installs an unrelated project): install it from git.
`uv tool install` puts `memhub` on your PATH for every project, which is what Claude Code needs to start the server.

```bash
uv tool install "memhub[openai,mcp,sqlite] @ git+https://github.com/jooaobrum/memhub.git"   # or pipx install "..."
mkdir -p ~/.config/memhub && cd ~/.config/memhub              # one config for every project
echo "OPENAI_API_KEY=sk-..." > .env                          # read from next to memhub.yaml, from any folder
memhub init --db sqlite:///$HOME/.memhub/claude.db --embeddings openai:text-embedding-3-small \
  --chat openai:gpt-4.1-mini                                 # or your Postgres URL; also writes a starter memory.md
claude mcp add --scope user memhub -- memhub mcp             # every session, in every project, has the tools
```

That is enough: sessions search memory and propose new memories with the tools (see [Without ingest](#without-ingest)).
To also learn from past sessions, add the source:

```bash
memhub config set sources.claude_code '{kind: claude_code, projects: ["*my-repo*"]}'   # optional: default is every project
memhub ingest --source claude_code --dry-run                 # what would be stored, written nowhere (costs tokens)
memhub ingest --source claude_code                           # reads ~/.claude/projects/*/*.jsonl
memhub queue                                                 # review what should be shared
```

Without the `mcp` extra, `memhub mcp` stops with `pip install memhub[mcp]`. With no `--chat` model, everything but
`ingest` works.

A session is read once it has been idle for `ingestion.segment_idle` (1 hour): `ingest` reports the others as
`threads_waiting`, and a later run reads them. `memhub config set ingestion.segment_idle 0s` reads them at once.

## Without ingest

Nothing has to be ingested. A new session calls `propose_memory` when you tell it something worth keeping ("remember
that we lint with ruff"); the memory waits as a candidate until you approve it, then every later session in that
project finds it with `search_memory`:

```bash
memhub queue                    # what sessions proposed, with the quote they took it from
memhub approve <id>             # active from now on; or: memhub reject <id> --note "..."
```

## The source: `claude_code`

`memhub ingest --source claude_code` needs no `sources:` entry. To narrow it, add one:

```yaml
sources:
  claude_code:
    kind: claude_code
    path: ~/.claude/projects    # the default
    projects: ["*my-repo*"]     # project folder globs (default: all)
    user: anna                  # default: MEMHUB_USER, else your OS user
```

| Transcript | memhub |
| --- | --- |
| a session (`<session>.jsonl`) | a thread: its watermark makes a second ingest skip what it already read |
| the project folder (`-Users-me-code-my-repo`) | the `workspace_id` |
| a line's `uuid` | the message id quoted in the evidence |
| user and assistant text | the messages the extractor reads |
| tool calls, tool results, thinking, subagent lines, injected and synthetic text, Claude Code's own wrappers (`<command-…>`, `<system-reminder>`, …) | left out |

The transcript format is not a public contract, so the reader is tolerant: a line it cannot read is counted in the
run summary's `skipped_lines` and the next line is read.

## The server: `memhub mcp`

A stdio MCP server with two tools, the same ones `MemoryToolkit` gives any agent:

- `search_memory(query, type?)` - your active user memories and the workspace's, nearest first.
- `propose_memory(type, fields, evidence?, scope?)` - stores a **candidate** and answers
  `{"id", "event": "PENDING", "status": "candidate"}`. Nothing an agent proposes is used until someone approves it
  in `memhub queue`.

Who the calls are for:

| | First of |
| --- | --- |
| workspace | `--ws`, `MEMHUB_WORKSPACE`, else the folder Claude Code started the server in, named the way Claude Code names project folders (every character outside `A-Za-z0-9` becomes `-`) - the same `workspace_id` the source writes, so a session searches its own project's memories with no setup |
| user | `--user`, `MEMHUB_USER`, else your OS user |

`memhub use` is the CLI's own context and is not read here: picking a workspace at the terminal never moves your
Claude Code sessions to it. To browse a project's memories from the CLI, `memhub list` shows each `workspace_id`.

Each server process is one session: its proposals carry `run_id = mcp-<hex>` and `agent_id = mcp`. The server
refuses to start (exit 1, the reason on stderr) when the config has no database or embedder, or the ledger needs
`memhub init` or `memhub migrate`.
