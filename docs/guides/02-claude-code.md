# Give Claude Code a memory

Claude Code starts every session from nothing. With memhub, each session gets two tools:

- `search_memory` finds what you (or your past sessions) taught it.
- `propose_memory` saves something new. It waits for your approval before any session uses it.

There are two ways to fill the memory, and you can use both:

- **Let sessions propose.** Connect the server and say "remember that…". Nothing is ingested.
- **Learn from past sessions.** `memhub ingest --source claude_code` reads your transcripts.

## Setup (once per machine)

Claude Code starts `memhub mcp` itself, from whichever project you open. So memhub must be on your PATH, and its config
must live somewhere every project can find. `uv tool install` and `~/.config/memhub` cover both.

```bash
uv tool install "memhub[openai,mcp,sqlite] @ git+https://github.com/jooaobrum/memhub.git"
mkdir -p ~/.config/memhub && cd ~/.config/memhub
echo "OPENAI_API_KEY=sk-..." > .env            # read from next to memhub.yaml, whatever folder Claude Code is in
memhub init --db sqlite:///$HOME/.memhub/claude.db \
  --embeddings openai:text-embedding-3-small --chat openai:gpt-4.1-mini
claude mcp add --scope user memhub -- memhub mcp
```

`--chat` is only needed for `ingest`. Without it, everything else works.

## Way 1: sessions propose, you approve

In any project:

```
> Please remember for future sessions: in this project I always use ruff for linting and black is forbidden.
  Also, our CI runs on GitHub Actions.

  I saved both in memhub as candidates. memhub won't use them until someone reviews and approves them.
  1. Preference (linting): "Always use ruff for linting in this project; black is forbidden."
  2. Fact: "The project's CI runs on GitHub Actions."
```

Review them from a terminal:

```bash
memhub queue                    # each candidate with the quote it came from
memhub approve <id>             # or: memhub reject <id> --note "not a rule, a one-off"
```

From then on, any new session in that project uses them, even when you don't mention memory:

```
> Add a short CONTRIBUTING.md section describing the lint command and CI for this repo.

  ## Linting
  We use ruff (black is not used in this project) ...
  ## Continuous Integration
  CI runs on GitHub Actions ...
```

## Way 2: learn from past sessions

```bash
memhub config set sources.claude_code '{kind: claude_code, projects: ["*my-repo*"]}'
memhub ingest --source claude_code --dry-run    # `preview`: what would be stored, written nowhere
memhub ingest --source claude_code
```

Narrow `projects` first. Without it, every session on your machine is read and sent to the model.

From two short sessions ("I always want type hints and I prefer pytest", "we deploy on Fly.io, staging is Postgres
16", "never use print for logging, we use structlog", "I'm the tech lead, name is Joana") one run stored:

```
profile     Is the tech lead named Joana.
preference  logging     Wants structlog for logging, never print.
preference  type_hints  Wants type hints to always be used in code.
preference              Prefers pytest over unittest for testing.
fact        The staging database is Postgres 16.
fact        The team deploys the service on Fly.io.
```

Only user and assistant text is read. Tool calls, tool results, thinking, sub-agents and Claude Code's own wrappers
are left out. A session is read once it has been idle for an hour (`ingestion.segment_idle`). Newer ones are
reported as `threads_waiting` and picked up by the next run.

## Which memories a session sees

Each project is its own workspace, named after its folder the way Claude Code names it. A session sees its own
project's memories and yours as a user. To share across projects, start the server with a fixed workspace:
`claude mcp add --scope user memhub -- memhub mcp --ws team`. Picking a workspace for the CLI with `memhub use`
never moves your sessions.

## Inspect and correct

```bash
memhub list                     # shows each memory's workspace_id; pass --ws <that id> to narrow
memhub edit <memory_id> -f fix.json
memhub archive <memory_id>      # hidden from every session, history kept
```

Reference: [docs/claude-code.md](../claude-code.md).
