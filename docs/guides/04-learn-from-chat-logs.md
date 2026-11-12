# Learn from your chat logs

Your support bot already writes its conversations to logs. This guide turns those logs into reviewed memory, entirely
from the CLI, on Postgres:

- per customer: how they want to be answered;
- per company: account facts shared by every agent, after a person approves them.

## 1. A database and a config

```bash
docker run -d --name memhub-pg -e POSTGRES_USER=memhub -e POSTGRES_PASSWORD=memhub -e POSTGRES_DB=memhub \
  -p 127.0.0.1:5433:5432 pgvector/pgvector:pg16          # or any Postgres with the pgvector extension
uv pip install "memhub[openai] @ git+https://github.com/jooaobrum/memhub.git"
printf 'OPENAI_API_KEY=sk-...\nPGPASSWORD=memhub\n' > .env

memhub init --project support \
  --db 'postgresql://memhub:${PGPASSWORD}@localhost:5433/memhub' \
  --embeddings openai:text-embedding-3-small --chat openai:gpt-4.1-mini
memhub doctor
```

Write `${PGPASSWORD}` exactly like that, in single quotes. The yaml then keeps the reference instead of the
password, and the value is read from the environment or `.env`.

`init` writes a starter `memory.md`. Replace it with what a support bot should remember:

```markdown
---
types:
  account:                                  # a company's plan and contact, shared by every agent after review
    fields:
      company: {type: str, description: "the customer's company"}
      plan: {type: "str?", description: "subscription plan, if said"}
      contact: {type: "str?", description: "admin or main contact person"}
    content_template: "{company}[ - plan {plan}][, contact {contact}]"
    scopes: [workspace]
  preference: {class: "memhub.types:Preference", retrieval: always, keyed: true, strict_keys: false, scopes: [user],
               keys: {language: "language of the answers", detail: "length of the answers"}}
  fact: {class: "memhub.types:Fact", scopes: [workspace]}     # a company fact, shared after review
  ticket: {fields: {symptom: str, root_cause: str, resolution: str, outcome: str}, extract: false, on_remember: true,
           scopes: [workspace], content_template: "Symptom: {symptom} Root cause: {root_cause} Resolution: {resolution}"}
---
# What to remember

Customer-support agent. Keep durable facts the customer states about their company, account, setup and integrations
(`account`, `fact`), and how they want answers (`preference`).

- Never keep questions, one-off requests or incidents that are already solved, or what the agent says.
- Write `content` as your own third-person sentence ("Wants every answer in Spanish"), never the customer's words
  copied as they are; the verbatim words go in the evidence quote.
```

That gives four types:

- `account`, a company's plan and contact, shared;
- `preference`, how a customer wants answers, per user;
- `fact`;
- `ticket`, a solved problem saved with `/remember`.

`account`, `fact` and `ticket` use the long form, so their fields carry a description and they are shared with the
workspace (a candidate until approved); see [docs/memory.md](../memory.md).

## 2. Map your log format

memhub reads JSONL with your own field names. One line per turn, as an app writes it:

```json
{"conv": "t-100", "customer": "globex-anna", "company": "globex", "id": "m1", "ts": "2026-11-10T09:00:00Z",
 "q": "Hi, I'm Anna, the admin at Globex. We're on the Enterprise plan ...", "a": "Hi Anna! How can I help today?"}
```

```bash
memhub config set sources.jsonl '{kind: jsonl, path: logs/chats.jsonl, fields: {thread_id: conv, user_id: customer,
  workspace_id: company, message_id: id, timestamp: ts, user_content: q, assistant_content: a}}'
```

## 3. Look before writing

```bash
memhub ingest --source jsonl --dry-run
```

```json
"preview": [
  {"outcome": "created", "type": "account", "scope": "workspace", "status": "candidate",
   "content": "Globex - plan Enterprise, contact Anna", "workspace_id": "globex", "user_id": null},
  {"outcome": "created", "type": "preference", "scope": "user", "status": "active",
   "content": "Wants short bullet-point answers (reads on mobile)", "workspace_id": "globex", "user_id": "globex-anna"}
]
```

The dry run makes the full set of model calls, then rolls everything back. The preview lists each memory that would be
written and each candidate dropped, with its reason. A question such as "How do I rotate an API key?" produces nothing.

## 4. Ingest, then review

```bash
memhub ingest --source jsonl      # idempotent: a thread's watermark skips what was already read
memhub queue --ws globex          # shared candidates, conflicts first
memhub approve <id> --note "checked in the CRM"
```

Schedule `memhub ingest` (cron, Airflow, a Kubernetes CronJob). It exits non-zero when a segment fails, and that
segment is retried on the next run.

## 5. Correct and audit

```bash
memhub use globex --user globex-anna
echo '{"content": "Wants short bullet-point answers; reads on mobile."}' > fix.json
memhub edit <memory_id> -f fix.json     # version 2, verified: later ingests never overwrite it
memhub history <memory_id>              # v1 superseded, v2 active, who and when
memhub runs --last 1                    # what each run proposed, created, merged, and dropped by reason
```

Approving needs the `workspace_admin` role. The CLI acts as `cli:<your OS user>` with the roles in `MEMHUB_CLI_ROLES`.
