# Habitantes example

A chatbot for *Habitantes de Grenoble* (Brazilians living in Grenoble) with memhub long-term memory. It shows the whole loop:
ingest past conversations, review the memories, then chat with an agent that reads them.

| File | What it is |
|---|---|
| [memhub.yaml](memhub.yaml) | Config: user scope, JSONL source, feedback signals, areas (visa, banks, housing...), extraction prompts |
| [data/](data) | Small **synthetic** sample: two users (`demo-ana`, `demo-bruno`). The real pilot data is not published |
| [chatbot.py](chatbot.py) | Terminal chatbot: LangChain agent + `MemoryMiddleware` |
| [functional_check.py](functional_check.py) | End-to-end PASS/FAIL of every CLI command against the ledger |
| [last_run.json](last_run.json) | Run summary of the last pilot ingest (5 users, 37 segments): what was proposed, kept and dropped |

## Run it

From the repo root, with Postgres up (`docker compose up -d`), `MEMHUB_DATABASE_URL` set as in the main README, and `OPENROUTER_API_KEY` /
`OPENAI_API_KEY` in a git-ignored `.env`:

```bash
uv pip install -e ".[dev,openai]" langgraph
C=examples/habitantes/memhub.yaml
memhub init -c $C
memhub ingest --source jsonl -c $C
memhub list -c $C            # what was extracted
memhub queue -c $C           # what waits for approval
python examples/habitantes/chatbot.py demo-ana
python examples/habitantes/functional_check.py
```

Try asking `demo-ana` "quando meu titre vence?": the answer should use the memory that it expires on 30 May and that she prefers short answers.
