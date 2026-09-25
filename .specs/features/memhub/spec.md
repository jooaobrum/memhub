# memhub — Reviewed Long-Term Memory for Agents

**Status:** v1 and v1.1 built · **v1.2 (profile, preferences, areas, terms; smaller and safer) specified
2026-09-25** · **Owner:** 1 engineer · **Type:** standalone Python package that works with any agent, with
Habitantes as the first pilot

### What v1.2 changes (read this first)

**Principle: less is more.** Run 5 stored 31 memories, and 14 of them were context Episodes ("Question about
X") that added nothing. A memory system that grows without limit, or that invents things, is worse than a small
one. v1.2 therefore shrinks and organises what is stored. Every rule below is meant to keep the number of
memories low and to keep every memory backed by something the user or a tool actually said.

- **Kinds, by what they answer.**
  - **Core (most important):** `Profile` (who the person is), `Preference` (how they want the agent to
    respond) and `Fact` (their situation in one *area*, shown as area pages).
  - **Secondary (on, but less important):** `Episode` (a real case: situation, action, outcome) and `Term` (a
    glossary entry shared by the workspace). They get smaller caps and a smaller share of the prompt, and they
    are built after the core.
  - `Skill` is unchanged.
  - **`Plan` is removed** (added in v1.1, removed in v1.2). A stated intention is not stored as its own kind.
    Jev agreed with only 1 of the 5 plans in run 5, and the fact behind a plan (studies, a residence status)
    goes to Profile or a Fact.
- **Profile and Preference are slots.** A closed list of keys in config, one active row per (user, key), and
  a new value replaces the old one as a new version. A slot can never hold duplicates.
- **Areas** group Facts (and Episodes where a project wants it). An area is a row of type `area` and memories
  point to it with a link, so there is no new table. The config lists seed areas, and the model may propose
  new ones within a cap. An area page (title, summary, details, last updated) is assembled from its rows.
- **Terms** are glossary entries (for Habitantes, e.g. `CNH` = `carteira de motorista` = `permis de conduire`,
  or `CAF`), with aliases and related terms. They are created only from an explicit definition, always go to
  review, and are used to expand search queries.
- **The automatic context Episode of v1.1 is removed.** An area gives a claim its context. An Episode exists
  only when the conversation describes a case with an action and an outcome.
- **Each project turns on only the kinds it needs** in its own config; nothing in the code is specific to
  Habitantes (see the table under Decisions).

### What v1.1 added to the built v1

Pilot run 3 showed memories with no sense of time. Every row is dated by ingestion, not by when the user spoke.
Short-lived needs ("urgently looking for a dentist") never expire. The same idea is stored as a Fact and as an
Episode with no link between them. Inferred claims look the same as stated ones
([research.md §10](research.md#10-graph-memory-post-and-pilot-run-3-findings)). v1.1 fixes this on the same
two tables, with no new table and no graph database:

- **Time:** `observed_at` + a `valid_from` / `valid_until` window. Relative time is resolved to absolute dates at extraction.
- **Expiry:** a past `valid_until` makes a memory *stale*: out of search and the prompt, but kept.
- **`Plan`:** a new built-in type for stated intentions. (Removed in v1.2.)
- **`assertion`:** `stated | inferred`.
- **Links:** a `links` column. v1.1 created `derived_from` (claim → its context Episode). v1.2 removes that link
  and reuses the column for `in_area`.

## Problem statement

Agents forget everything between conversations, and the existing memory frameworks write memories
automatically, with no guarantee the memories are true. We need a small memory service that any agent can
plug in. It must turn conversation traces into **typed, evidence-backed, versioned** memories, and it must
never publish shared knowledge without a person approving it.

## Context: two layers

**memhub (works with any project).** A package with a CLI, configured by one `memhub.yaml` per deployment.
Everything specific to a project (memory types, entity types, input sources, LLM and embedding models, table
prefix) is config.

**Habitantes pilot (this repo). Scope: testing extraction on JSONL traces, and nothing else.** memhub runs
offline over `logs/interactions.jsonl` (+ `logs/feedback.jsonl`), and we inspect what it extracts. The
Habitantes agent is **not** changed, no middleware is wired in, and there are no project-specific types. From
v1.2 the pilot also has one shared (workspace) kind, the glossary `Term`; every other memory is user scope.

Research behind the decisions: [research.md](research.md). Tables, config and pipeline: [design.md](design.md).

## Decisions

- **Name:** `memhub` (Python package + CLI `memhub`).
- **Scopes:** `workspace` (shared by every user of a workspace) and `user` (private). An `agent` scope can be
  added later without a migration. Pilot: **`user`**, plus **`workspace` for the glossary `Term` only** (v1.2),
  with a single implicit workspace.
- **Typed memories:** every memory is an instance of a registered Pydantic type that extends `MemoryBase`
  (`content`, `entities`, `tags`). Built-in types: `Profile`, `Preference`, `Fact`, `Episode`, `Term`, `Area`,
  `Skill`. Projects register their own types in config. Each record stores its `type` and `schema_version`,
  and old records are never migrated automatically.
- **Entities:** memories can reference entities (`{type, id}`, e.g. a machine). The entity types come from
  config. Entities are used to filter and boost search results, and to decide when two claims can conflict.
- **Storage:** Postgres + pgvector, **two tables**: `<prefix>_memory` (one row per version; this is the
  ledger) and `<prefix>_memory_runs` (ingestion watermark + signals + dropped candidates).
- **Models:** the LLMs (extractor, judge) and the embedding model are set in config as `{provider, model,
  base_url?, api_key_env, params?}` and built with LangChain `init_chat_model` / `init_embeddings`.
  Switching between OpenRouter, OpenAI and Anthropic is a config change only. Changing the embedding model
  requires `memhub reembed`.
- **Lifecycle:** `candidate → active | rejected`, `active → archived`. An edit creates a new version and marks
  the old one `superseded`. Delete is permanent and only allowed for rejected candidates or a user's own data.
- **Review:** workspace memories are approved by a `workspace_admin`. User memories activate automatically,
  are labelled **unverified** in the prompt, and only their owner can edit or delete them. Promoting a user
  memory to workspace scope always goes to the admin queue. memhub has no authentication of its own: callers
  pass an `actor` (id + roles).
- **Ingestion (Loop 1):** batch pull through source adapters. v1 adapters: **JSONL** (the only one used by
  the pilot) and **MLflow traces**.
- **Behaviour loop (Loop 2)**, i.e. patterns and root causes across many threads producing `Skill`
  proposals: **next version**. v1 records the signals it will need.
- **Time (v1.1):** every memory has `observed_at` and an optional validity window `valid_from` / `valid_until`.
  `observed_at` is the timestamp of the newest evidence message, not the ingestion time. Together with the
  version chain, this records both *when a claim is true* and *when memhub learned it*, so "what was true
  before" is answered by the superseded versions. The extractor resolves relative time ("next year", "I'm 18",
  "for 3 months", "I just moved") into absolute dates, using the message's timestamp. The verbatim quote stays
  in the evidence. Example: "I'm 18 and will apply to Grenoble next year", said on 2026-03-10, becomes a `Fact`
  "Born around 2008 (18 on 2026-03-10)" with no expiry. (v1.1 also stored the intention as a `Plan` valid until
  2027-12-31; v1.2 has no Plan kind, so the intention is not stored.)
- **Expiry (v1.1):** a memory whose `valid_until` has passed is **stale**. Stale is computed from the date, not
  stored as a status. The row stays `active` and queryable, but search and the prompt skip it by default.
  Nothing is deleted. `valid_until` is chosen in this order:
  1. the date stated in the text;
  2. the type's `ttl` (e.g. Episodes);
  3. the candidate's `durability` (`stable | ongoing | temporary`), mapped to a TTL in config (e.g. `null`,
     1 year, 30 days).

  This stops what reaches the prompt from growing forever. A repeated claim renews its memory.
- **Plans (v1.1, removed in v1.2):** `Plan` was a stated intention with `status: open | done | abandoned`. It is
  removed (ticket 27). The judge verdict `updates` it introduced is generic and stays: a later statement of a new
  situation (a new city, a new residence status) supersedes the older fact with a new version.
- **Assertion (v1.1):** `stated` (the user said it) or `inferred` (deduced from what they said, e.g. "asks
  about student housing" → probably a student). Inferred memories score lower and are labelled `(inferred)` in
  the prompt.
- **Links (v1.1, changed in v1.2):** relations are stored on the memory row as `links: [{kind, memory_id}]`,
  with no new table. v1.1 created `derived_from` (claim → its context Episode). **v1.2 removes it** and creates
  one kind, `in_area` (memory → its area row). The rest of the link vocabulary is still open (see Open
  questions).
- **Kinds per project (v1.2).** Each project turns on the kinds it needs in its config; a kind with
  `extract: false` is never offered to the extractor. Habitantes:

  | Kind | Habitantes | Priority |
  |---|---|---|
  | Profile | on | core |
  | Preference | on | core |
  | Fact, in an area (area pages) | on | core |
  | Episode (a real case) | on | secondary |
  | Term (glossary, workspace scope) | on | secondary |
  | Skill | off (Loop 2, later) | – |

  Secondary kinds have smaller caps (Episode `max_active`, `terms.max_per_workspace`), are injected after the
  core within the prompt budget, and are built after it.
- **Profile and Preference (v1.2).** Both are *keyed*: the keys come from config with a one-line description
  each, e.g. Profile `nationality, city, age, residence_status, studies, work, family`, Preference `language,
  scope, style, detail`. A key may set `mutable: false` (Habitantes: `nationality`). `age` is stored with its
  as-of date ("Is 18 (as of 2026-08-10)") and expires by its durability, so it is never shown as current a year
  later. A candidate whose key is not in the list is dropped. There is one active row per
  (owner, type, key), and a new value supersedes the old one, so a move from Grenoble to Paris is an update
  and not a second memory. Profile and Preference are about the person and have no area.
- **Areas (v1.2).** An area is a row of type `area` (`title`, `description`, `summary`). Facts (and Episodes,
  when the project sets `area: optional`) link to it with `in_area`; a memory may have one to three areas.
  - Config gives **seed areas** (Habitantes: 19, e.g. Visto & Residência, Moradia & CAF). A seed becomes a
    row for a user only when that user's first memory lands in it.
  - The model **may propose a new area** when no existing one fits. A new title is compared with the owner's
    existing areas, and a near-duplicate merges into the existing area. New areas are marked `proposed` until
    an admin confirms, renames or merges them, and there is a cap per owner (default 25 per user, 40 per
    workspace). Over the cap, the candidate is dropped.
  - A Fact that fits no area is dropped (`no_area`). That is a deliberate junk filter.
  - Area is a **boost** in search, never a hard filter, so a wrong area cannot hide a memory.
- **Area page (v1.2).** A view, not a stored document: the area row's title and summary, then its active,
  non-stale linked memories newest first (the details), and `last_updated` = the newest `observed_at`. The
  **summary is derived**: one cheap LLM call per area touched by a run, written only from the linked active
  rows, stored as a new version of the area row (`created_by = summarizer`) and labelled auto-summary. It is
  never evidence, and it never adds a claim that no row supports.
- **Terms (v1.2).** A `Term` has `term`, `expansion` (optional), `aliases` (interchangeable, e.g. CNH ↔ carteira
  de motorista ↔ permis de conduire) and `related` (same topic, but not the same thing). It is workspace scope,
  so an admin approves it through the existing review queue. This makes Habitantes use workspace scope for terms
  only; `scopes` becomes `[user, workspace]`.
  - A term is proposed **only when a message explicitly defines it**, and the quote is required. Every alias
    must appear in the evidence. Nothing is created from co-occurrence in this version.
  - Search expands the query with the aliases of matching active terms before embedding. `related` terms only
    boost.
  - Cap: 300 terms per workspace, 5 aliases per term.
- **Upsert and conflicts (v1.2).** The same person keeps talking for weeks, so new statements update, refine or
  contradict memories that already exist. The rules, applied the same way every time:
  - Order by when it was said (`observed_at`), never by ingestion. An older statement never overwrites a newer
    one: the same value adds evidence, a different value is dropped (`outdated`).
  - The same value adds evidence and renews the memory, with no new version.
  - A different, newer value: the judge says `updates` (replace, e.g. 18 → 19 or Grenoble → Paris), `extends`
    (complementary, merged into one statement from the two statements' own words) or `conflicts` (a candidate
    for review, the active row untouched). A `mutable: false` key never updates: a different value is a conflict.
  - A memory a person has verified or edited is never superseded by the extractor: a differing statement becomes
    a conflict candidate.
  - A stated memory is never superseded by an inferred one.
  - One slot gets one value per segment (the later message wins), and processing a segment twice changes nothing.
  - Every superseded version stays readable (`memhub history`), and the ledger invariants (one active version
    per memory, one active row per slot, contiguous versions, a quote on every row) hold after every step.
- **Volume guardrails (v1.2).** Every one of these is a config value with a conservative default.
  - `max_candidates` per segment (3, exists) and `max_active` per (owner, type) (exists).
  - `allow_inferred: false`: inferred candidates are dropped (`inferred`). The `assertion` field stays, and a
    project can turn inference on.
  - The area caps above, the term caps above, and one active row per slot for Profile and Preference.
  - Grounding is unchanged: no quote, no memory. Areas and terms follow the same rule.
- **Episode (v1.2).** Created only when the conversation describes a real case, meaning a situation, the actions
  taken and the outcome, all taken from the text. There is no automatic context Episode. A secondary kind: small
  cap, low type prior.
- **Retrieval trace (v1.1):** no memhub table. The middleware writes the `memory_id@version` of every injected
  memory into the agent's state and trace metadata, so anything in the context can be traced back to its
  version and evidence.

## What the system does

1. **Ingest.** `memhub ingest --source <name>` reads traces through an adapter, groups them into threads, and
   processes any new **segment** whose thread has been idle for ≥ `segment_idle` (default 1 h). When a thread
   has been idle for ≥ `thread_close` (default 7 d) it gets one **final pass** over the whole conversation.
   Progress is saved per thread, so re-running does nothing unless `--reprocess` is passed.
2. **Extract.** A prefilter (no LLM) skips trivial segments. One structured LLM call proposes 0–3
   candidates of the enabled types. Each candidate has evidence quotes, a claim source
   (`user|tool|assistant`), a utility score, entities, `durability`, `assertion`, the validity dates it can
   resolve from the text, and (v1.2) a `key` for Profile and Preference, or `areas` for Facts. The transcript
   shows each message's date, and the prompt lists the owner's existing areas.
3. **Check grounding.** Candidates whose quotes are not in the segment are dropped. `Fact`s supported only by
   the assistant's own words are dropped.
4. **Score.** An admission score from utility, evidence strength, novelty, type prior and implicit signals,
   with weights in config. Below the threshold the candidate is dropped and logged. A user correction
   triggers extraction directly.
5. **Reconcile.** A near-duplicate adds evidence to the existing memory (`seen_count` counts only independent
   sources) and renews it: its `observed_at` and `valid_until` move to the later of the two values. A later
   state of the same thing (the judge says `updates`) supersedes the existing memory with a new version. A
   likely contradiction becomes a candidate linked to the existing memory with `conflicts_with`. Nothing is
   deleted automatically.
6. **Route.** Set `valid_until` (text → type `ttl` → `durability` TTL) and the `in_area` links (v1.2: resolve
   or create the area rows first). User scope becomes `active` (unverified). Workspace scope becomes a
   `candidate` in the review queue.
6b. **Summarise (v1.2).** At the end of a run, regenerate the summary of every area that gained or changed a
   row (one cheap call per area).
7. **Review (CLI).** List the queue, then approve, reject, edit, archive or delete. Add memories by hand from a
   JSON file validated against the type's schema.
8. **Serve (library, not used by the pilot).** `MemoryMiddleware` for LangChain agents. At thread start it
   loads the user's Profile and Preferences (an "About the user" block) and the skill index, and keeps them
   fixed for the thread. Once per user turn it runs a search (with the glossary expansion and an area boost)
   and injects a `<memory>` block with IDs, plus the page of the query's best-matching area. Stale memories
   are skipped, and each item shows its "as of" date and `(inferred)` when applicable. It gives the agent the tools
   `search_memory`, `load_skill` and `propose_memory`. `propose_memory` only ever creates a candidate, and
   `/remember` uses it. The injected `memory_id@version` list is written to the trace metadata.

## Acceptance criteria

1. WHEN `memhub ingest --source jsonl` runs on the pilot logs THEN the system SHALL create memories only for
   segments idle ≥ `segment_idle`, and SHALL save a watermark per thread in `<prefix>_memory_runs`.
2. WHEN `memhub ingest` runs a second time with no new trace lines THEN it SHALL create no memories and make
   no LLM calls.
3. WHEN a candidate's evidence quote is not a substring of the segment's messages THEN the system SHALL drop
   it and record the reason `ungrounded` in `dropped`.
4. WHEN a `Fact` candidate's only evidence has `claim_source = assistant` THEN the system SHALL drop it and
   record the reason `assistant_only`.
5. WHEN a candidate is ≥ `reconcile.duplicate` similar to an active memory of the same type and scope THEN the
   system SHALL append its evidence to that memory instead of creating a row, and SHALL increment `seen_count`
   only if the source thread is new for that memory.
6. WHEN a workspace-scope candidate is created THEN its status SHALL be `candidate`, and it SHALL NOT be
   returned by search until an actor with `workspace_admin` approves it.
7. WHEN a user-scope memory is created THEN its status SHALL be `active`, and search SHALL return it marked
   `verified=false`.
8. WHEN an active memory is edited THEN the system SHALL insert version N+1 as `active` and set version N to
   `superseded` in one transaction; at no time SHALL a `memory_id` have more than one active version.
9. WHEN `memhub add --type <t> --file x.json` receives fields that fail the type's Pydantic validation THEN
   the system SHALL reject the input with the validation errors and write nothing.
10. WHEN a thumbs-down in `feedback.jsonl` or a correction is detected in a segment THEN the system SHALL
    record it in `signals` for that run.
11. WHEN `memhub delete --user <id>` runs THEN the system SHALL permanently delete all user-scope rows of that
    user, and drop that user's rows from `<prefix>_memory_runs`.

**v1.1**

12. WHEN a memory is created or evidence is merged into it THEN its `observed_at` SHALL be the timestamp of its
    newest evidence message (never the ingestion time), and each evidence entry SHALL carry its message's
    timestamp.
13. WHEN a candidate has no `valid_until` THEN the system SHALL set `valid_until = observed_at + ttl`, taking
    `ttl` from the type's config if set, and otherwise from `ttl[durability]`. A `stable` candidate of a type
    with no `ttl` SHALL have `valid_until = null`.
14. WHEN a memory's `valid_until` is in the past THEN search and the middleware SHALL NOT return it unless
    `include_stale` is set, and its `status` SHALL stay unchanged.
15. WHEN a candidate is merged into an existing memory THEN that memory's `observed_at` and `valid_until` SHALL
    each become the later of the two values (`null` counts as later than any date).
16. ~~Context Episode and `derived_from` links.~~ **Removed in v1.2** (see 19). It was implemented in v1.1 and
    is reverted by ticket 26.
17. WHEN a candidate has `assertion = inferred` THEN its evidence feature SHALL use the inferred value, and
    search results SHALL include `assertion`.
18. WHEN the judge returns `updates` for a candidate THEN the system SHALL insert it as version N+1 of the
    existing memory and set version N to `superseded`, instead of creating a conflict candidate.

**v1.2**

19. WHEN a segment yields claims THEN the system SHALL NOT create any automatic context Episode, and the
    extraction output SHALL have no `context` field. An Episode is only stored when the extractor proposes one
    as a candidate with all its fields taken from the text.
20. WHEN `allow_inferred` is false (the default) THEN a candidate with `assertion = inferred` SHALL be dropped
    with the reason `inferred`. A type with `extract: false` SHALL NOT be offered to the extractor.
21. WHEN a Profile or Preference candidate has a key that is not in that type's config THEN the system SHALL
    drop it (`unknown_key`). WHEN it has a valid key THEN it SHALL supersede the active row with the same
    (owner, type, key) as a new version, so at most one row per (owner, type, key) is active.
22. WHEN a Fact is admitted THEN it SHALL have one to three `in_area` links to area rows of the same owner,
    and a Fact with no area SHALL be dropped (`no_area`). WHEN a linked seed area has no row for that owner
    THEN the system SHALL create it once, and only then.
23. WHEN the extractor proposes a new area THEN the system SHALL compare its title with the owner's areas and
    merge it into the closest one at or above `areas.merge_similarity`; otherwise it SHALL create the area
    with `proposed = true`. WHEN the owner is at the area cap THEN the candidate SHALL be dropped (`area_cap`).
24. WHEN `memhub page` runs for an owner and area THEN it SHALL return the area's title and summary, its
    active non-stale linked memories newest first, and `last_updated` equal to the newest `observed_at`.
25. WHEN a run adds or changes a row in an area THEN the system SHALL regenerate that area's summary once, at
    the end of the run, from the linked active rows only, and SHALL store it as a new version marked
    auto-summary. An area with no active linked rows SHALL get no summary call.
26. WHEN a message explicitly defines a term THEN the system SHALL create a `Term` as a workspace-scope
    `candidate` with the defining quote as evidence, and SHALL drop it (`ungrounded`) if any alias is not in
    the evidence. It SHALL NOT be returned by search until an admin approves it. No term SHALL be created from
    co-occurrence.
27. WHEN a search query contains an active term or one of its aliases THEN the system SHALL add that term's
    other aliases to the query before embedding, and SHALL use `related` terms only to boost.
28. WHEN the term or area count of an owner reaches its cap THEN the system SHALL create no more of them and
    SHALL record the drop reason in the run.
29. WHEN the middleware starts a thread THEN it SHALL load the user's active Profile and Preference rows as one
    "About the user" block (stale ones skipped), and WHEN it handles a turn THEN it SHALL also inject the page
    of the area that best matches the query, within `max_chars`, and record every injected `memory_id@version`.
30. WHEN a candidate's `observed_at` is older than the active row's THEN the row's value SHALL NOT change: the
    same value adds evidence only (and `observed_at` does not move back), and a different value is dropped
    with the reason `outdated`.
31. WHEN a keyed candidate has a different value from the active row of its slot, is newer, and the row is not
    verified THEN the judge verdict SHALL decide: `updates` supersedes with a new version, `extends` creates a
    new version whose content merges the two statements, and `conflicts` creates a candidate with
    `conflicts_with` and leaves the active row unchanged. WHEN the key has `mutable: false` THEN a differing
    value SHALL become a conflict candidate unless the verdict is `extends`. The judge SHALL NOT be called when
    the normalised texts are equal, and at most once per candidate.
32. WHEN the active row is verified THEN the extractor SHALL NOT supersede it: a differing candidate becomes a
    conflict candidate, and the same value adds evidence.
33. WHEN a candidate is inferred THEN it SHALL NOT supersede a stated row.
34. WHEN two candidates from one segment target the same slot THEN only the one from the later message SHALL
    continue, and the other SHALL be dropped as `same_slot_in_segment`.
35. WHEN the same segment is processed twice THEN no version SHALL be created, `seen_count` SHALL NOT change,
    and no evidence entry SHALL be duplicated (evidence is deduplicated by message id).
36. WHEN any sequence of candidates has been applied THEN at most one version per `memory_id` SHALL be active, at
    most one row per (owner, keyed type, key) SHALL be active, versions SHALL be contiguous from 1, every active
    row SHALL have at least one quote, and every superseded version SHALL be readable through `memhub history`.

## DS / GenAI requirements

### 1) Data contracts
- **Inputs:** traces normalised by an adapter into an `Interaction` (`thread_id, user_id, workspace_id,
  message_id, role, content, timestamp, trace_id, metadata`). The JSONL adapter maps fields through config.
  Pilot mapping: `chat_id → thread_id` and `user_id`, `user_query`/`answer` → user/assistant messages,
  `feedback.jsonl` → signals.
- **Outputs:** rows in `<prefix>_memory` whose `payload` validates against the registered type at the stored
  `schema_version`.
- **Invalid input:** malformed JSONL lines are skipped and counted in the run summary. A line missing
  `thread_id` or `message_id` is skipped.

### 2) Grounding & evidence
- Every version SHALL carry `evidence[]` with `{source, trace_id, message_id, observed_at, quote,
  claim_source}`. Memories added by hand carry `{source: "manual", actor, reference?}`, and their
  `observed_at` is the time they were added unless the input gives one.
- Search results SHALL include `memory_id`, `version`, `verified`, `assertion`, `observed_at`, `valid_until`,
  `areas` (titles) and the evidence sources, so the agent can cite them and see how old they are.
  (v1.1 returned the linked Episode as `context`; v1.2 removes that.)
- An area summary is derived text: it SHALL be labelled auto-summary and SHALL NOT be used as evidence for
  another memory.

### 3) Safety & compliance
- Pilot logs contain personal data (Telegram `chat_id` and message text). memhub SHALL store only the evidence
  quotes, not full transcripts. Per-user delete (criterion 11) satisfies the erasure right in
  [PRIVACIDADE.md](../../../docs/PRIVACIDADE.md).
- Content written through `propose_memory` or `memhub add` SHALL pass a prompt-injection pattern check before
  it is stored.

### 4) Quality metrics (pilot)
- **Extraction precision:** the share of extracted memories a human judges correct and useful, measured on a
  labelled sample of ≥ 50 memories. Target ≥ 0.8.
- **Hallucination rate:** memories that fail a human grounding check. Target 0, given the mechanical quote check.
- **Drop analysis:** a breakdown of dropped candidates by reason, used to tune the admission weights.

### 5) Latency / cost budget
- Offline batch only. At most 1 extraction call per segment, plus at most 1 judge call per candidate that
  falls in the conflict band.
- Cheap models by default (config). The run summary reports tokens and cost.

### 6) Failure modes + safe behaviour
- **LLM or embedding call fails:** the segment is not marked processed, so it is retried on the next run.
  Nothing is partially written; each segment is one transaction.
- **Extractor returns invalid structured output:** zero candidates, and the run records `extract_error`.
- **Ambiguous conflict:** it becomes a candidate with `conflicts_with`, never an automatic overwrite.

### 7) Observability
- Every run logs its `run_id`, and every memory version references the `trace_id`s it came from.
- The run summary shows threads and segments processed, candidates proposed, dropped (by reason), merged,
  created, tokens and cost.

## Non-goals (v1)

- Loop 2 (pattern mining across threads → skill proposals); evals that check skill changes.
- A Hub or web UI and an HTTP API. The CLI is the only review interface.
- A graph database, a separate links table, multi-hop traversal. v1.2 follows at most one `in_area` hop.
- Finding terms by co-occurrence, an `area` hard filter, and any shared (workspace) Facts or general knowledge
  extracted from the assistant's answers. These are later versions. `Plan` is not deferred: it is removed.
- Forgetting-curve decay, automatic archiving or deletion of stale memories.
- "As of" queries ("what did we believe in March?") beyond reading the version chain by hand.
- A memhub table for retrieval logs (the trace metadata holds it).
- Checking candidate claims against real systems (environment probing).
- Pilot: any change to the Habitantes agent, middleware integration, MLflow, workspace-scope memories, custom types.

## Assumptions

- A Postgres instance with pgvector is available. The pilot adds one to `docker-compose.yml` for local runs;
  today the repo has no Postgres.
- In the Habitantes logs one `chat_id` is both one user and one continuous thread (Telegram/WhatsApp DMs).
- The pilot reuses the project's existing providers: OpenRouter for the chat LLMs (same models as
  `config/base.yaml`) and OpenAI `text-embedding-3-small` for embeddings.

## Done when

- [ ] The `memhub` package has a config loader, type registry, JSONL + MLflow adapters, the pipeline (steps
      1–6), the ledger (2 tables) and the CLI (`ingest`, `queue`, `approve`, `reject`, `edit`, `archive`,
      `delete`, `add`, `list`, `search`).
- [ ] `memhub.yaml` for Habitantes: prefix `habitantes`, JSONL source over `logs/`, built-in types only, `user` scope only.
- [ ] Acceptance criteria 1–11 are covered by tests. A Postgres test fixture with pgvector is used for the
      ledger tests.
- [ ] A pilot run on the real `logs/interactions.jsonl` produces a run summary and a labelled precision sample.
- [ ] `MemoryMiddleware` exists with unit tests against a fake agent (not wired into Habitantes).

**v1.1**

- [ ] `Plan` type (removed again in v1.2), the new columns (`observed_at`, `valid_from`, `valid_until`, `durability`, `assertion`,
      `links`), and `memhub init` adding them to existing tables.
- [ ] The extraction prompt and contract carry dates, `durability`, `assertion` and the context Episode. The
      pilot `memhub.yaml` instructions no longer tell the model to rewrite dated statements into timeless ones.
- [ ] Acceptance criteria 12–18 are covered by tests.
- [ ] A pilot re-run (`--reprocess` on a cleared ledger) shows no ingestion-dated rows, no Fact/Episode pairs
      with the same content, and short-lived needs with a `valid_until`.

**v1.2**

- [ ] Ticket 26 reverts the automatic context Episode. Tickets 27–36 remove `Plan` and add the conservative
      defaults, Profile and Preference slots, seed and model-proposed areas, area pages with a derived summary,
      terms with query expansion, upsert and conflict handling tested over time (34), the middleware blocks and
      the Habitantes re-run.
- [ ] Acceptance criteria 19–36 are covered by tests, including the scenario suite and the seeded randomised
      test of ticket 34.
- [ ] The Habitantes re-run (from empty tables) stays small (target: no more than 2–3 active memories per
      user on average), has no memory without a quote, no Fact without an area, and no slot with two active
      rows. Quality is judged with Jev and an agent read, as in run 5, and stated as such.

## Open questions

- **Link vocabulary (to discuss).** v1.2 creates only `in_area`. Other candidates: `supports` (independent
  evidence for a claim), `outcome_of` (a case → what resolved it), `related` (memory → memory). For each one,
  we still have to decide who creates it (extractor, judge, reviewer) and what retrieval does with it.
  `conflicts_with` stays a column for now.
- **Discovering terms.** v1.2 creates terms only from explicit definitions. Proposing them from terms that
  appear together across threads (for example CNH and "carteira de motorista") is a later, propose-only step.
- **Updates to a fact.** v1.1's judge verdict `updates` supersedes a fact (for example a new city) when a later
  message states the new situation. It stays, now without Plans. Slots already handle the same case by key.

## Reference

- Research: [research.md](research.md) · Design: [design.md](design.md)
- Pilot inputs: [logs/interactions.jsonl](../../../logs/interactions.jsonl), written by
  [logging.py](../../../api/src/habitantes/infrastructure/logging.py) (`log_interaction`, `log_feedback`).
