# memhub as memory for Claude Code

Two pieces: a source that turns past Claude Code sessions into memories, and an MCP server that gives every new
session `search_memory` and `propose_memory` over them.

```bash
pip install "memhub[mcp,sqlite]"
memhub init --db sqlite:///$HOME/.memhub/claude.db --embeddings openai:text-embedding-3-small \
  --chat openai:gpt-4.1-mini --preset assistant      # or your Postgres URL
memhub ingest --source claude_code                   # reads ~/.claude/projects/*/*.jsonl
memhub queue                                         # review what should be shared
claude mcp add memhub -- memhub mcp                  # from now on, every session has the tools
```

Without the `mcp` extra, `memhub mcp` stops with `pip install memhub[mcp]`.

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
| workspace | `--ws`, `MEMHUB_WORKSPACE`, `memhub use`, else the folder Claude Code started the server in, named the way Claude Code names project folders (every character outside `A-Za-z0-9` becomes `-`) - the same `workspace_id` the source writes, so a session searches its own project's memories with no setup |
| user | `--user`, `MEMHUB_USER`, `memhub use`, else your OS user |

Each server process is one session: its proposals carry `run_id = mcp-<hex>` and `agent_id = mcp`. The server
refuses to start (exit 1, the reason on stderr) when the config has no database or embedder, or the ledger needs
`memhub init` or `memhub migrate`.
