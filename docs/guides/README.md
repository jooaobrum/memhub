# memhub guides

Practical, tested walkthroughs. Every command and output here comes from end-to-end runs on a clean machine (see
[.specs/e2e/scenarios.md](../../.specs/e2e/scenarios.md)).

| Guide | You will |
|---|---|
| [Quickstart](01-quickstart.md) | install, initialize, add and search from the CLI and from Python |
| [Give Claude Code a memory](02-claude-code.md) | connect the MCP server; let sessions propose memories, or learn from past sessions |
| [Add memory to your own agent](03-add-memory-to-your-agent.md) | configure memhub in code; search/add, framework-neutral tools, LangChain middleware |
| [Learn from your chat logs](04-learn-from-chat-logs.md) | map your JSONL logs, preview, ingest on a schedule, review and correct |
| [Field-service agents that share fixes](05-field-service-agents.md) | turn one technician's fix into reviewed team knowledge |
| [Tune memhub to your own domain](06-your-own-domain.md) | one `memory.md`: your own types, a closed list of areas, your own rules; read the drops to adjust |
