# memhub — Design

See [spec.md](spec.md) for intent and decisions, and [research.md](research.md) for the reasoning behind them.

## Boundary

`memhub` is a standalone package. It lives at `packages/memhub/` in this repo until it is split out. It imports
**nothing** from `habitantes`. Habitantes is only a consumer: a `memhub.yaml` at the repo root plus the JSONL
logs. The dependency goes one way only (Habitantes → memhub).

```
packages/memhub/src/memhub/
  config.py        memhub.yaml → Settings (pydantic-settings, ${ENV} interpolation)
  types.py         MemoryBase, EntityRef, Fact, Preference, Episode, Skill + registry
  store.py         Postgres ledger: the two tables, lifecycle transitions, vector search
  sources/
    base.py        SourceAdapter protocol → Iterator[Interaction]
    jsonl.py       field-mapped JSONL (+ optional signal file)
    mlflow.py      MLflow traces → Interaction
  pipeline/
    segment.py     thread grouping, segment_idle / thread_close, watermarks
    prefilter.py   rule-based skip
    extract.py     structured LLM extraction (one call per segment)
    ground.py      quote check, assistant-only rule, injection scan
    score.py       admission score
    reconcile.py   duplicate merge / conflict detection (+ judge)
    route.py       status by scope policy
  signals.py       correction detector, feedback mapping
  service.py       MemoryService (public API used by CLI + middleware)
  middleware.py    LangChain AgentMiddleware + tools
  cli.py           `memhub` (typer)
packages/memhub/tests/
```

Data flow: `source → segment → prefilter → extract → ground → score → reconcile → route → <prefix>_memory`.
Each segment's processing is committed as one transaction, together with its `<prefix>_memory_runs` row.

## Types

```python
class EntityRef(BaseModel):
    type: str                  # must be in config.entity_types
    id: str

class MemoryBase(BaseModel):
    content: str               # one human-readable statement; this is what gets embedded
    entities: list[EntityRef] = []
    tags: list[str] = []

class Fact(MemoryBase): ...
class Preference(MemoryBase):
    key: str                   # e.g. "language", "answer_style"; one active row per (user, key)
class Episode(MemoryBase):
    situation: str; actions: str; outcome: str
class Skill(MemoryBase):
    name: str; description: str; body: str
# (v1.1 had a `Plan` type here. It is removed in v1.2, ticket 27.)

# v1.2
class Profile(MemoryBase):
    key: str                   # must be in types.profile.keys, e.g. "nationality", "city"; one active row per (owner, key)
class Term(MemoryBase):        # workspace scope; content = "CNH (carteira nacional de habilitação): carteira de motorista"
    term: str
    expansion: str | None = None
    aliases: list[str] = []    # interchangeable: query expansion
    related: list[str] = []    # same topic: boost only
class Area(MemoryBase):        # the header of an area page; created by the pipeline, not by an extraction candidate
    title: str
    description: str = ""
    summary: str = ""          # derived, see "Areas and pages"
    proposed: bool = False     # true when the model created it and nobody has confirmed it
```

`Preference` is keyed in the same way as `Profile`: its `key` must be in `types.preference.keys`. Reconcile treats
every type marked `keyed: true` in config alike, so a new keyed type needs no new code.

Time, `durability`, `assertion` and `links` are **row columns**, not payload fields. They mean the same thing
for every type, so project types get them without redeclaring them, and search can filter on them in SQL.

**There is no context Episode (v1.2).** A claim's context is its area. An `Episode` is an ordinary candidate that
the extractor proposes only when the text gives a situation, the actions and an outcome. It is a secondary kind.

Project types subclass `MemoryBase` and are registered in config as `module:Class`. The registry stores
`(type_name, schema_version) → class`, and a stored payload is always validated with the class for its own
version. The extractor's structured-output schema is a discriminated union of the enabled types, generated
from the registry.

## Storage (Postgres + pgvector)

Tables are named `<prefix>_memory` and `<prefix>_memory_runs`. `memhub init` creates them (idempotent DDL, no
migration tool in v1).

```sql
CREATE TABLE {p}_memory (
  id             uuid PRIMARY KEY,            -- version id
  memory_id      uuid NOT NULL,               -- stable across versions
  version        int  NOT NULL,
  type           text NOT NULL,
  schema_version int  NOT NULL,
  scope          text NOT NULL,               -- workspace | user
  workspace_id   text NOT NULL,
  user_id        text,                        -- required when scope = user
  status         text NOT NULL,               -- candidate | active | rejected | archived | superseded
  verified       boolean NOT NULL,            -- true only after human approval / manual add
  content        text NOT NULL,
  payload        jsonb NOT NULL,
  entities       jsonb NOT NULL DEFAULT '[]',
  embedding      vector({dims}),
  evidence       jsonb NOT NULL,              -- [{source, trace_id, thread_id, message_id, observed_at, quote, claim_source}]
  seen_count     int  NOT NULL DEFAULT 1,     -- independent threads only
  observed_at    timestamptz NOT NULL,        -- v1.1: newest evidence message's timestamp, never ingestion time
  valid_from     timestamptz,                 -- v1.1: null = unknown
  valid_until    timestamptz,                 -- v1.1: null = no expiry; in the past = stale
  durability     text,                        -- v1.1: stable | ongoing | temporary (from the extractor)
  assertion      text NOT NULL DEFAULT 'stated', -- v1.1: stated | inferred
  links          jsonb NOT NULL DEFAULT '[]', -- v1.1: [{kind, memory_id}]; v1.1 writes only kind=derived_from
  score          real,
  conflicts_with uuid,                        -- memory_id
  created_by     text NOT NULL,               -- actor id | "extractor"
  created_at     timestamptz NOT NULL DEFAULT now(),
  reviewed_by    text,
  reviewed_at    timestamptz,
  review_note    text,
  UNIQUE (memory_id, version)
);
CREATE UNIQUE INDEX ON {p}_memory (memory_id) WHERE status = 'active';
CREATE INDEX ON {p}_memory (workspace_id, scope, status, type);
CREATE INDEX ON {p}_memory (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX ON {p}_memory USING gin (entities);
CREATE INDEX ON {p}_memory USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON {p}_memory USING gin (links);                 -- v1.1: "what links to this Episode?"

CREATE TABLE {p}_memory_runs (
  id              uuid PRIMARY KEY,
  run_id          uuid NOT NULL,              -- one CLI invocation
  source          text NOT NULL,
  thread_id       text NOT NULL,
  workspace_id    text,
  user_id         text,
  first_message_id text NOT NULL,
  last_message_id text NOT NULL,              -- watermark
  last_message_at timestamptz NOT NULL,
  final_pass      boolean NOT NULL DEFAULT false,
  signals         jsonb NOT NULL DEFAULT '[]', -- [{kind: correction|feedback_down|feedback_up|error|rephrase, message_id, detail}]
  dropped         jsonb NOT NULL DEFAULT '[]', -- [{candidate, reason}]  purged after retention.dropped_days
  status          text NOT NULL,              -- ok | extract_error
  tokens_in int, tokens_out int, cost_usd real,
  processed_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON {p}_memory_runs (source, thread_id, processed_at DESC);
```

**How lifecycle operations map to SQL**

| Operation | SQL effect |
|---|---|
| approve | `candidate → active`, `verified=true`, set `reviewed_*`. If the row has `conflicts_with`, the reviewer chooses: `keep_old` (this row → rejected), `replace` (old active → archived), `keep_both` |
| reject | `candidate → rejected` |
| edit | insert version N+1 (`active`; `verified=true` when an admin edits) and set N to `superseded`, in one transaction. An edit from the agent or extractor creates a new `candidate` instead |
| archive | `active → archived` |
| delete | hard delete; allowed only when `status=rejected`, or when the actor owns the user-scope row |
| merge duplicate | `evidence = evidence \|\| new`; `seen_count += 1` only if the thread_id is new for this memory |

A full action history is not kept (accepted trade-off). If it's ever needed, add `events jsonb` to the row.

**Upgrading an existing ledger (v1.1).** `memhub init` adds the new columns with `ADD COLUMN IF NOT EXISTS`.
Existing rows get `observed_at = created_at`, which is wrong for pilot rows because they carry the ingestion
date. So the pilot ledger is cleared and re-ingested with `--reprocess` rather than backfilled.

**Stale** is computed, not stored: `status = 'active' AND valid_until < now()`. No job changes rows when a
memory expires.

**v1.2 adds no column and no table.**
- `links` (v1.1) holds `{kind: "in_area", memory_id: <area memory_id>}`. The GIN index answers "which rows are
  in this area?".
- An **area** is a row with `type = 'area'`, `scope`/`user_id` like any memory (a user-scope area belongs to one
  user, a workspace area to the workspace), `payload = {title, description, summary, proposed}` and `content =
  "<title>: <summary>"` so that it embeds. Its versions record every summary change.
- A **term** is a row with `type = 'term'`, `scope = 'workspace'`, `status = 'candidate'` until approved.
- A **Profile or Preference slot** is an ordinary row; "one active per (owner, type, key)" is enforced by
  reconcile (supersede), and additionally by a partial unique index on `(user_id, type, payload->>'key')` where
  `status = 'active'` and the type is keyed.

## Pipeline

**Segmenting.** Interactions are grouped by `thread_id` and sorted by timestamp. A segment is made of the
messages after the thread's last watermark, and it is processed only if its newest message is older than
`now - segment_idle`. The final pass runs once when the thread has been idle for `thread_close`: it sends the
whole thread to the extractor, restricted to `Episode` and episode-like project types.

**Prefilter (no LLM).** Skip the segment if it has fewer than `min_user_turns` user turns, or if every turn
matches `skip_when` (e.g. the pilot's `intent in [greeting, out_of_scope]`). Signals are still recorded for skipped
segments.

**Extraction contract.** One call per segment, using `llm.extractor` with structured output:

```json
{"candidates": [{
  "type": "fact", "scope": "user",
  "fields": {"content": "Has a Brazilian driving licence", "entities": [], "tags": []},
  "areas": [{"existing": "documents"}],      // v1.2: 1-3 of the listed area keys, or {"new": {"title": "...", "description": "..."}}
  "evidence": [{"message_id": "#2", "quote": "eu ja tenho a brasileira", "claim_source": "user"}],
  "utility": 4,
  "applies_generally": true,
  "durability": "stable",                    // v1.1: stable | ongoing | temporary
  "assertion": "stated",                     // v1.1: stated | inferred
  "valid_from": null,                        // v1.1: ISO date, only when the text gives one
  "valid_until": null                        // v1.1: ISO date, only when the text gives one
},
{"type": "profile", "scope": "user",
 "fields": {"key": "nationality", "content": "Is Brazilian", "entities": [], "tags": []},   // v1.2: key from config; no areas
 "evidence": [{"message_id": "#1", "quote": "sou brasileira", "claim_source": "user"}],
 "utility": 5, "applies_generally": true, "durability": "stable", "assertion": "stated"}]}
```

There is no `context` field (v1.2). `areas` is required for a type configured with `area: required`, allowed
for `area: optional`, and absent for a type with no area setting (Profile, Preference, Term). `key` is an
enum of the configured keys, built from config the way the type union already is.

The prompt says that most segments should produce no candidates, allows at most `max_candidates` (3), and
says to prefer statements the user made over the assistant's claims.

**v1.1 prompt rules.**
- **Dates.** The transcript shows each message's date: `[#2 2026-03-10] user: ...`. The model resolves
  relative time against that date, and never writes relative time into `content`:
  - "I'm 18" → "Born around 2008 (18 on 2026-03-10)", `stable`;
  - "next year" → `valid_until: 2027-12-31`;
  - "for 3 months" → `valid_until` 3 months after the message.
- **Intentions.** There is no Plan kind (v1.2). A stated intention ("I'll apply next year") is not stored; the
  durable fact behind it (studies, a residence status) goes to Profile or a Fact.
- **Durability.**
  - `stable`: identity, documents held, past events;
  - `ongoing`: studies, job, where they live;
  - `temporary`: a current need or errand.
- **Assertion.** `inferred` whenever the content goes beyond what the user literally said.
- **Slots (v1.2).** Person attributes go to `profile` with a key from the list (each key has a one-line
  description in the prompt). How the person wants the agent to answer goes to `preference`. Anything that
  matches no key is not a profile fact.
- **Areas (v1.2).** The prompt lists the owner's existing areas with their descriptions. The model must reuse
  one when it fits and propose a new one only when none does. A Fact that fits no area is not stored.
- **Episodes (v1.2).** Propose an Episode only when the text gives all of its fields. Never invent a missing one.
- **Terms (v1.2, projects with `term` enabled).** Propose a `term` only when a message explicitly defines it
  ("CNH significa carteira nacional de habilitação", "CNH é a mesma coisa que carteira de motorista"). Every
  alias must appear in the quote.

The pilot `memhub.yaml` instructions must drop the example that rewrites "deciding whether to apply now or next
year" into a timeless fact. With Plan removed, that message yields only the durable facts in it (for example
that the user is a student), and the intention is not stored.

**Validity (v1.1, in route).**
- `observed_at` is the newest timestamp among the candidate's evidence messages.
- `valid_until` is chosen in this order:
  1. the candidate's own date;
  2. `observed_at + types.<t>.ttl`;
  3. `observed_at + ttl[durability]`;
  4. `null`.
**Grounding checks (rules).**
- Each quote must appear in the content of its `message_id`, compared after normalising whitespace and case.
  Otherwise the candidate is dropped as `ungrounded`.
- `type == fact` with every `claim_source == assistant` → dropped as `assistant_only`. The agent's own output
  may only appear inside an `Episode`.
- Text matching the injection patterns → dropped as `injection`.
- v1.2, before scoring:
  - `assertion == inferred` and not `guardrails.allow_inferred` → dropped as `inferred`.
  - A keyed candidate whose `key` is not in its type's `keys` → dropped as `unknown_key`.
  - A `term` alias that is not in the evidence quotes → dropped as `ungrounded`.
  - A candidate of a type with `area: required` and no area → dropped as `no_area`.

**Admission score.**
`score = Σ wᵢ·fᵢ`, with each fᵢ in [0, 1]:

| Feature | How it is computed |
|---|---|
| `utility` | (utility − 1) / 4 |
| `evidence` | 1.0 if user/tool evidence is present and `assertion = stated`; 0.6 if `inferred` (v1.1); 0.4 for assistant-only (Episodes) |
| `novelty` | 1 − highest cosine similarity to active memories of the same type and scope |
| `type_prior` | from config, per type |
| `signals` | 0.5 + 0.5 for a correction or thumbs-up, − 0.5 for a thumbs-down, rephrase or error; clamped to [0, 1] |

If `score < threshold`, the candidate is dropped as `low_score`. A correction signal forces the segment through
the prefilter, but the candidate still has to pass the score.

**Reconcile.** For a **keyed** type (Profile, Preference): find the active row M with the same (owner, type, key).
If there is none, create. If there is one, apply the upsert policy below; no similarity search is needed. For every
other type, search the top 5 active memories (plus pending candidates) of
the same type, scope and owner; **v1.2: only rows that share at least one area with the candidate are compared**
(rows and candidates with no area are compared as before). Stale memories are included, so a repeated claim
renews its memory instead of creating a new one.
- Similarity ≥ `duplicate`: merge into that memory. `observed_at` and `valid_until` each become the later of
  the two values (`null` is later than any date).
- Similarity within `conflict_band` and sharing an entity, or with no entities on either side: ask
  `llm.judge` for `same | updates | conflicts | unrelated`. The judge sees both statements with their
  `observed_at` dates.
  - `updates` (v1.1): the new statement is a later state of the same thing, e.g. a new city or residence status.
    → supersede the existing memory with a new version.
  - `conflicts` → create a candidate with `conflicts_with`.
- Otherwise: a new memory.

**Upsert policy (v1.2, ticket 34).** A candidate C against the active row M of its slot (or the closest row in the
same area for a non-keyed type). Rules are checked in this order, and the first that applies decides:

| # | Condition | Outcome |
|---|---|---|
| 1 | C's `observed_at` is older than M's | same value: add evidence only, `observed_at` not moved back. Different value: drop `outdated` |
| 2 | M is verified (a person confirmed or edited it) | same value: add evidence. Different value: conflict candidate (`conflicts_with`), M stays active |
| 3 | C is inferred and M is stated | drop (`inferred`), M untouched |
| 4 | Normalised texts equal, or judge `same` | merge evidence, renew `observed_at` / `valid_until`, `seen_count` for a new thread only |
| 5 | Judge `extends` | new version N+1 with merged content (only the two statements' words), both evidences kept |
| 6 | Judge `updates` and the key is mutable (default) | supersede: new version N+1, N kept as history |
| 7 | Judge `updates` and the key is `mutable: false`, or judge `conflicts` | conflict candidate, M stays active |
| 8 | Judge `unrelated` (non-keyed only) | create a new memory |

The judge sees both statements with their `observed_at` dates and answers `same | extends | updates | conflicts |
unrelated`. It is skipped in rule 4's equal-text case, and called at most once per candidate. A stale M
(`valid_until` passed) is treated like any other M, so a repeated value renews it and a different one supersedes.

**Within a segment.** Before reconcile, when several candidates target the same slot, keep the one from the latest
message and drop the others as `same_slot_in_segment`.

**Idempotence.** `merge_evidence` deduplicates by `(source, message_id)`. A segment whose message ids are all
already in M's evidence creates nothing and does not touch `seen_count`.

**Terms.** A definition of an existing term (case-insensitive) adds aliases up to the cap. An alias already owned
by a different active term produces a conflict candidate for the admin, not an overwrite.

**Route.** User scope → `active`, `verified=false`. Workspace scope → `candidate`. A `Preference` replaces the
active preference with the same `key`, as a new version. v1.1:
- Route sets `observed_at`, `valid_until` and `valid_from` (see Validity).
- v1.2: before writing, route resolves each candidate's areas (see "Areas and pages") and writes
  `links = [{kind: "in_area", memory_id: <area memory_id>}, ...]`. When a claim merges into an existing memory,
  the links are appended if they are not already there.

**Search (v1.1, v1.2).**
- Filter: `valid_until IS NULL OR valid_until > now()`, unless `include_stale`.
- v1.2: the query is first expanded with the aliases of matching active terms. When the query's best area is
  known (nearest area description by embedding, or passed by the caller), rows in that area get a rank boost.
  It is a boost, never a filter.
- Results carry `observed_at`, `valid_until`, `assertion` and `areas` (titles of the `in_area` rows).

## Areas and pages (v1.2)

**Resolving areas (in route).** For each area named by a candidate:
1. `{existing: key}`: find the owner's area row for that seed key. If the owner has none, create it from the
   seed (title, description) once.
2. `{new: {title, description}}`: embed `title: description` and compare with the owner's area rows. At or above
   `areas.merge_similarity` (default 0.88) the candidate uses that area instead; otherwise create the area row
   with `proposed = true`. If the owner already has `areas.max_per_user` (or, for workspace, `max_per_workspace`)
   areas, drop the candidate as `area_cap`.
3. A candidate keeps at most 3 areas, and the first is its primary one.

Admin actions: `memhub areas` lists an owner's areas with counts and a `proposed` flag, and `memhub areas merge
<from> <to>` moves the `in_area` links of one area to another as new versions and archives the source. Renaming
and confirming a `proposed` area is an ordinary `edit`.

**Page.** `MemoryService.page(owner, area)` returns `{title, summary, details, last_updated}`. `details` are the
active, non-stale rows with an `in_area` link to that area, newest `observed_at` first, capped at
`areas.page_max_items` (default 15). `last_updated` is the newest `observed_at` among them. It is a query, so
there is nothing to keep in sync.

**Summary.** After the last segment of a run, every area row that gained or changed a linked row gets one call to
`llm.judge` (cheap model): the prompt contains only the area title and the `content` of its active linked rows,
and asks for two sentences at most, without adding anything that is not in the rows. The result is written as a
new version of the area row with `created_by = "summarizer"`. It is skipped when the area has no active rows,
when the row set is unchanged since the last summary, and on `--dry-run`.

## Terms (v1.2)

- **Creation.** The extractor may return a `term` candidate (workspace scope, so it lands as a `candidate` in the
  review queue). Grounding checks that every alias is in the quote. Reconcile merges a candidate whose `term`
  matches an active term (case-insensitively) by adding its aliases as a new version.
- **Caps.** `terms.max_per_workspace` (300) and `terms.max_aliases` (5); over the cap the candidate is dropped
  (`term_cap`).
- **Query expansion.** `MemoryService.search` loads the workspace's active terms (small enough to hold in memory
  per call), finds the ones whose `term` or an alias occurs in the query, and appends their other aliases to the
  query text before embedding. `related` entries only add a small rank boost.
- **Not built:** discovering terms from co-occurrence. A later job could propose them, always as candidates.

**Signals.**
- The feedback file is mapped via config (`rating: down → feedback_down`).
- The correction detector is regex-first ("não, …", "na verdade", "errado", "not X but Y"), with an optional
  cheap LLM fallback turned on in config. The pilot keeps it regex-only.

## Config

```yaml
# memhub.yaml (Habitantes pilot)
project_prefix: habitantes
database_url: ${MEMHUB_DATABASE_URL}
workspace_default: habitantes
llm:                                      # see "Models" below; same models as config/base.yaml
  extractor: {provider: openai, model: google/gemini-2.5-flash-lite, base_url: https://openrouter.ai/api/v1, api_key_env: OPENROUTER_API_KEY}
  judge:     {provider: openai, model: google/gemini-2.5-flash,      base_url: https://openrouter.ai/api/v1, api_key_env: OPENROUTER_API_KEY}
embeddings:
  provider: openai
  model: text-embedding-3-small
  api_key_env: OPENAI_API_KEY
  dims: 1536
entity_types: []                          # pilot: none
scopes: [user, workspace]                 # workspace is used only by `term` (the shared glossary)
types:                                    # v1.2. Core: profile, preference, fact. Secondary: episode, term.
  profile:    {class: memhub.types:Profile,    keyed: true, retrieval: always, type_prior: 0.8, max_chars: 1000,
               keys: {nationality: {description: "Country of citizenship", mutable: false},
                      city: "City where the user lives",
                      age: "Age, written with its as-of date, e.g. 'Is 18 (as of 2026-08-10)'",
                      residence_status: "Visa or residence permit held", studies: "What and where they study",
                      work: "Job or field of work", family: "Family living with or depending on them"}}   # a key is a description, or {description, mutable}
  preference: {class: memhub.types:Preference, keyed: true, retrieval: always, type_prior: 0.8, max_chars: 600,
               keys: {language: "Language to answer in", scope: "How far the answer should go",
                      style: "Tone and format", detail: "Level of detail"}}
  fact:       {class: memhub.types:Fact,       retrieval: search,  type_prior: 0.6, area: required, max_active: 30}
  episode:    {class: memhub.types:Episode,    retrieval: search,  type_prior: 0.4, ttl: 180d, max_active: 10}   # secondary; a real case only
  term:       {class: memhub.types:Term,       retrieval: search,  scopes: [workspace]}                        # secondary; explicit definitions only
  skill:      {class: memhub.types:Skill,      retrieval: index_then_load, extract: false}
areas:                                    # v1.2
  open: true                              # the model may propose new areas
  max_per_user: 25
  max_per_workspace: 40
  merge_similarity: 0.88
  page_max_items: 15
  seeds:                                  # the 19 Habitantes areas live in memhub.yaml; two shown here
    - {key: visa_residence, title: "Visto & Residência", icon: "🛂", description: "Visa, residence permit, titre de séjour, renewals, nationality"}
    - {key: housing_caf,    title: "Moradia & CAF",       icon: "🏠", description: "Renting, housing help (CAF), deposits, landlords"}
guardrails: {allow_inferred: false}       # v1.2: inferred candidates are dropped
terms: {max_per_workspace: 300, max_aliases: 5}   # only used by projects that enable the `term` type
ttl: {stable: null, ongoing: 365d, temporary: 30d}   # v1.1: by durability, used when neither the text nor the type sets valid_until
sources:
  jsonl:
    kind: jsonl
    path: logs/interactions.jsonl
    one_line_per: turn                    # turn → emits a user and an assistant message
    fields:
      thread_id: chat_id
      user_id: chat_id
      message_id: message_id
      timestamp: timestamp
      trace_id: trace_id
      user_content: user_query
      assistant_content: answer
      metadata: [intent, category, confidence, error]
    signals:
      path: logs/feedback.jsonl
      join_on: {message_id: message_id, thread_id: chat_id}
      map: {rating: {down: feedback_down, up: feedback_up}}
ingestion:
  segment_idle: 1h
  thread_close: 7d
  min_user_turns: 1                       # pilot logs are often single-turn
  max_candidates: 3
  skip_when: {intent: [greeting, out_of_scope]}   # values logged by the Habitantes agent
admission:
  weights: {utility: 0.35, evidence: 0.30, novelty: 0.20, type_prior: 0.10, signals: 0.05}
  threshold: 0.5
reconcile: {duplicate: 0.92, conflict_band: [0.80, 0.92]}
roles: {approve_workspace: [workspace_admin]}
retention: {dropped_days: 90}
```

### Models

memhub never imports a provider SDK directly. `config.py` turns each model entry into a LangChain object:

```python
init_chat_model(model=cfg.model, model_provider=cfg.provider,
                base_url=cfg.base_url, api_key=os.environ[cfg.api_key_env], **cfg.params)
init_embeddings(model=cfg.model, provider=cfg.provider, ...)
```

Switching provider is a config change only:

```yaml
# OpenRouter (any model, OpenAI-compatible API)
extractor: {provider: openai, model: google/gemini-2.5-flash-lite, base_url: https://openrouter.ai/api/v1, api_key_env: OPENROUTER_API_KEY}
# OpenAI
extractor: {provider: openai, model: gpt-4.1-mini, api_key_env: OPENAI_API_KEY}
# Anthropic
extractor: {provider: anthropic, model: claude-haiku-4-5-20251001, api_key_env: ANTHROPIC_API_KEY}
```

- `extractor` and `judge` are configured separately, so they can use different providers.
- Optional `params` (e.g. `temperature: 0`, `max_tokens`) are passed through unchanged.
- The provider's LangChain package must be installed. memhub declares extras for this: `memhub[openai]`,
  `memhub[anthropic]`.
- Structured output uses `with_structured_output`. At startup, memhub checks that each configured model
  supports it and fails fast if one doesn't.
- **Embeddings are different.** Anthropic has no embedding model, so embeddings need a provider that does,
  e.g. `openai`, `huggingface` (local) or `voyageai`. The `vector(dims)` column is fixed when the tables are
  created. `memhub init` saves the embedding model and dims, and every command refuses to run if the config no
  longer matches. After changing the embedding model, run `memhub reembed` to recompute every active version's
  embedding in batches and rebuild the index. Changing the chat model needs no migration.

A deployment that needs more would add `entity_types`, register its own types (`module:Class`) and an `mlflow`
source (`kind: mlflow, tracking_uri, experiment`). Which kinds are on, which keys and areas exist, and every cap
are config, so another project turns on a different set without any code change.

**Priority.** Core kinds (Profile, Preference, Fact/areas) are injected first. Secondary kinds (Episode, Term) get
smaller caps (`max_active`, `terms.max_per_workspace`) and are injected only in the space that is left.

## CLI

```
memhub init                                   create tables + extension; saves embedding model + dims
memhub reembed                                recompute embeddings after an embedding-model change
memhub ingest --source jsonl [--reprocess] [--dry-run] [--thread <id>]
memhub list   [--status ..] [--type ..] [--user ..] [--ws ..] [--stale]
memhub search "<query>" [--user ..] [--ws ..] [--include-stale]
memhub queue  [--ws ..]                        workspace candidates, conflicts first
memhub approve <id> [--resolve keep_old|replace|keep_both] [--note ..]
memhub reject  <id> [--note ..]
memhub edit    <id> --file fields.json
memhub archive <id>
memhub delete  <id> | --user <user_id>
memhub add --type <t> --scope <s> [--ws ..] [--user ..] --file x.json [--reference WO-123]
memhub runs   [--last N]                       run summaries, dropped-by-reason
memhub history <memory_id>                     every version: value, observed_at, evidence, creator (v1.2)
memhub page   --user <id> [--area <key|title>] an area page (all of the user's pages when --area is omitted)
memhub areas  [--user <id>] | merge <from> <to>   list areas with counts and `proposed`, or merge two
```

The CLI acts as `actor=cli:<os user>` with roles from `MEMHUB_CLI_ROLES` (default `workspace_admin`).

## Middleware (library; not wired into Habitantes)

`MemoryMiddleware(service, workspace_from=..., user_from=...)`, a LangChain `AgentMiddleware`:
- `before_agent`, first turn of a thread: load the `retrieval: always` types and the skill index (name +
  description), and cache them in agent state for the whole thread. v1.2: Profile and Preference rows form one
  "About the user" block (Profile first, each capped at its `max_chars`), which is where the agent learns who the
  person is and how to answer.
- `before_agent`, every user turn: `service.search(query=last user message, k, entity boost)`, then inject
  `<memory>` items as `[id|type|verified] content (as of 2026-03-10, inferred) (source)`. "inferred" appears only
  when `assertion = inferred`. Stale memories are never injected. v1.2: also inject the page of the area that best
  matches the query (title, summary marked auto-summary, top details), within `areas.page_max_chars`.
- Retrieval trace (v1.1): the `memory_id@version` of every injected item is appended to agent state
  (`memhub_injected`). When the host has a trace (MLflow), it is also written to that trace's metadata. memhub
  stores nothing for this.
- Tools: `search_memory(query, type?)`, `load_skill(name)`, `propose_memory(type, fields, evidence)` (always
  creates a `candidate`, even for user scope).

## Testing

- Unit: the registry and versioned validation, JSONL field mapping, segmenting and watermarks, grounding rules,
  the score, the reconcile decisions (with fake embeddings), the lifecycle transitions.
- Ledger: a pgvector Postgres in docker (`pgvector/pgvector:pg16`) via a pytest fixture, skipped if not available.
- Extraction: a fake LLM that returns fixed candidates, so tests are deterministic. The real-LLM pilot run is manual.
- v1.1 units:
  - `observed_at` comes from message timestamps;
  - the `valid_until` precedence (text → type ttl → durability ttl → null);
  - search excludes stale rows, and `include_stale` brings them back;
  - a merge renews `observed_at` and `valid_until`;
  - the inferred evidence feature;
  - the judge's `updates` verdict supersedes.
- v1.2 units:
  - no `context` in the extraction schema, and no automatic Episode;
  - `allow_inferred: false` drops inferred candidates, and a type with `extract: false` is not offered;
  - keyed types: unknown key dropped, same key supersedes, the partial unique index rejects a second active row;
  - area resolution: existing seed created once, `new` merges above the similarity, `proposed` below it, cap drops;
  - `no_area` drop, and reconcile comparing only rows that share an area;
  - `page()` ordering, `last_updated`, and the summary regeneration rules (once per touched area, none when empty,
    none when unchanged);
  - terms: explicit definition creates a candidate, an alias missing from the quote is dropped, the caps hold,
    and query expansion adds aliases while `related` only boosts.
- **Upsert and conflicts (ticket 34).** Scenario tests over time with real Postgres, a fake extractor and judge, and
  controlled message timestamps and `now`: 18 → 19, out-of-order ingestion, a repeated fact a month later, a city
  change, an immutable key, `extends` (nationality, family), a verified row, a stale row, inferred versus stated,
  two values in one segment, re-ingest, non-keyed facts in an area, terms, and an area merge. After every step the
  ledger invariants are checked (one active version per memory, one active row per slot, contiguous versions, a
  quote on every row, no duplicated evidence, no link to an archived area). A seeded randomised test (200
  sequences, an oracle written in the test) repeats this with random values, dates and verified edits.

  Time-dependent tests take `now` as a parameter instead of reading the clock.
