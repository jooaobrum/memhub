# memhub — Research Notes

**Date:** 2026-09-24 · **Purpose:** the findings behind the design decisions in [spec.md](spec.md).
Two of the requested sources (the Medium articles on graph memory and on Hermes) returned HTTP 403. For those
topics this note uses the Hermes official docs and the same author's related article instead.

## 1. Memory taxonomy (consensus across sources)

| Type | What | Where it lives in memhub |
|---|---|---|
| Working / short-term | Current thread state | The agent's checkpointer (not memhub) |
| Semantic, **profile** | One document per entity, always loaded | `Preference` |
| Semantic, **collection** | Open-ended facts, searched when needed | `Fact` + project types |
| Episodic | Past situations → actions → outcome | `Episode` + project types |
| Procedural | Instructions / rules that change behaviour | `Skill` |

- LangMem's point about profile vs collection: profiles are cheap and always current. Collections grow
  without limit and have to reconcile new facts with old ones (update / merge / invalidate).
- Procedural memory is often where agent behaviour visibly improves (LangChain). It is also the riskiest
  kind to change, so LangChain recommends evals for behaviour changes.

Sources: [LangChain — How to give your agent memory](https://www.langchain.com/blog/how-to-give-your-agent-memory),
[LangMem conceptual guide](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/).

## 2. When memories are written

- **During the conversation (the agent calls a memory tool):** saved immediately, but adds latency. In memhub
  this is `propose_memory` / `/remember`.
- **In the background (after the conversation):** deeper analysis with no latency cost. LangMem's
  `ReflectionExecutor` defers the work and cancels redundant runs. In memhub this is batch ingestion.
- LangChain's memory loop is **capture → analyse → update**, with three warnings: most trace data should
  *not* become memory; future runs must actually reload the updated context; behaviour changes need evals.

Sources: [LangMem delayed processing](https://langchain-ai.github.io/langmem/guides/delayed_processing/),
[LangChain blog](https://www.langchain.com/blog/how-to-give-your-agent-memory).

## 3. Hermes Agent: small, curated, loaded once per session

- Two capped files, MEMORY.md (~2.2k chars) and USER.md (~1.4k chars), loaded at session start **as a frozen
  snapshot**. Changes only show up in the next session, which keeps the prompt cache valid.
- Tool actions are `add` / `replace` / `remove`. When memory is full, the write **fails with an error**
  instead of silently dropping entries, and the agent has to consolidate.
- Memory writes are scanned for prompt injection and data exfiltration before they're accepted.
- Recall of past conversations is kept **separate** from memory: SQLite FTS5 (BM25 keyword search), with no LLM
  calls. Memory holds "what is always relevant"; session search answers "did we discuss X?".

→ memhub keeps the loaded-once snapshot for preferences and the skill index, and caps the preference profile.

Sources: [Hermes persistent memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory),
[Hermes memory code walkthrough](https://www.mmntm.net/articles/hermes-memory-architecture).

## 4. Graph and temporal memory

- Zep/Graphiti record when each fact was valid and when the system learned it. Old facts are invalidated, not
  deleted, so "what did we believe in March?" can be answered. This needs a graph database (Neo4j or FalkorDB).
- Shibui's DSE combines hybrid search (Elasticsearch), a Neo4j graph and Temporal workflows. It finds relations
  between memories with embeddings plus an LLM, scores memories with a forgetting curve, and archives them below 0.2.
- Surveys describe the memory lifecycle as extraction → storage → retrieval → evolution.

→ Not adopted for v1 (new infrastructure). memhub borrows two ideas: versions are superseded rather than
deleted, and `entities` references give lightweight graph-like filtering.
→ **v1.1** also borrows the time model, but keeps it on Postgres. A validity window (`valid_from` /
`valid_until`) plus `observed_at` records when a claim is true. The version chain already records when memhub
learned it. Expired memories become stale; they are not deleted. See §10.

Sources: [Shibui — Dynamic Agent Memory](https://shibuiyusuke.medium.com/dynamic-agent-memory-powered-by-a-search-engine-86eec6cd7479),
[Graph-Based Personalized Memory for LLM Agents](https://arxiv.org/pdf/2609.08599).

## 5. Off-the-shelf frameworks

| Framework | Model | Fit |
|---|---|---|
| Mem0 | An LLM extracts facts, then chooses Add/Update/Delete/No-op against similar memories. Postgres + pgvector self-hosted | Easy to plug in; no "verified" state; LLM-driven DELETE can [silently remove memories you need](https://dev.to/mukesh_13/mem0-auto-resolves-memory-conflicts-for-you-until-it-silently-deletes-one-you-still-need-4f4m) |
| Letta (MemGPT) | The agent manages its own memory like an operating system manages memory pages | Heavy and opinionated |
| Zep / Graphiti | Temporal knowledge graph | Needs a graph database |
| LangGraph Store | Generic key-value store with namespaces and pgvector search | No lifecycle, versions or review queue |

In practice, many teams report that one Postgres table with a pgvector column is enough.

→ memhub builds its own small ledger on Postgres + pgvector, because none of these frameworks offers
**human-reviewed, versioned** memory.

Sources: [Mem0 add](https://docs.mem0.ai/core-concepts/memory-operations/add),
[Mem0 breakdown](https://memo.d.foundation/breakdown/mem0),
[Q3 2026 comparison](https://mnemoverse.com/docs/library/ai-memory-solutions-2026-q3),
[LangGraph add-memory](https://docs.langchain.com/oss/python/langgraph/add-memory).

## 6. Write policy: deciding what becomes a memory

- **Extraction itself is the filter.** Store small, single-statement facts, not raw turns. Scoring every turn
  for importance (Generative Agents) costs one LLM call per write and the scores drift between models. Merge
  facts about the same entity **when writing**; keep eviction for compliance.
  ([Hindsight](https://hindsight.vectorize.io/blog/2026/05/21/agent-memory-consolidation))
- **A-MAC admission control (ICLR 2026):** score each candidate on utility, confidence (evidence support),
  novelty, recency and type prior. Only utility needs an LLM (~97% of the latency); the other four are rules
  (<65 ms). Beats fully LLM-driven policies and can be audited.
  ([A-MAC](https://arxiv.org/abs/2603.04549v1))
- **ProMem:** extract each type of information separately, check for completeness, and verify each extracted
  fact on its own. This reduces hallucinated memories compared with one summary prompt.
  ([ProMem](https://arxiv.org/abs/2601.04463))

→ memhub: one structured extraction call (which also returns utility), mechanical grounding checks, and a
rule-based admission score with weights in config.

## 7. False memories and provenance

- **GovMem ("When Not to Write Memory"):** repeated observations are **not independent evidence**. They may
  come from one shared source, the same prompt, or the agent's own earlier output. Governed writes cut false
  promotion from 0.597 to 0.040. In the real-world test, **zero candidates were safe to promote
  automatically.** ([arXiv 2607.02579](https://arxiv.org/abs/2607.02579))
- **Agent Zero Memory:** every item carries its origin, timestamp and a pointer to its evidence. Answers can
  only cite evidence that was actually retrieved, so fabrication is ruled out by design.
  ([arXiv 2608.29606](https://arxiv.org/abs/2608.29606))
- **Environment-probing curation:** a curator agent with read-only tools checks candidate claims against real
  systems before writing (pass rate 39% → 73%). It lists how extracting memories straight from conversations
  fails: memorising an instance's answer instead of a procedure, inheriting inefficient paths, claiming too
  broad a scope, and going stale. ([arXiv 2609.11060](https://arxiv.org/html/2609.11060v1))
- Other checks: link each memory to its source turn and verify it with NLI (entailment); span-level
  faithfulness checks. ([Evidence-tracing survey](https://arxiv.org/pdf/2606.04990))

→ memhub: every version stores evidence (verbatim quote, message ID, claim source). **Facts supported only by
the assistant's own words are rejected**; `seen_count` only counts independent sources; shared (workspace)
memories are always reviewed by a person. Checking against real systems (environment probing) is kept for a
later version.

## 8. Implicit feedback signals

- Negative: rephrasing or repeating the question, correcting the agent, abandoning after one turn, asking for
  a human, negative wording.
- Positive: confirming an action, returning later, copying the answer.
- These signals exist in every conversation but are weak individually.

→ memhub: signals adjust the admission score. The exception is a **user correction**, which directly triggers
extraction. All signals are recorded for the future behaviour loop (Loop 2).

Sources: [Nebuly — implicit feedback](https://www.nebuly.com/blog/explicit-implicit-llm-user-feedback-quick-guide),
[User Feedback in Human-LLM Dialogues](https://arxiv.org/pdf/2507.23158).

## 9. The trace → memory loop (LangChain diagram) and memhub

LangChain's diagram: Agent → traces (error, mismatch, correction) → *spot patterns → find root cause → propose
updates → validate* → updated memory used on the next run.

- **Loop 1 — knowledge, one thread at a time (v1):** extract → grounding checks → propose → human validation.
- **Loop 2 — behaviour, across many threads (next version):** group signals, find the root cause, propose
  `Skill` updates, validate with review and evals. v1 already records the signals Loop 2 will need.

## 10. Graph memory post and pilot run 3 findings

**Date:** 2026-09-25.

### The post: "Context → Action → Memory → Context"

Source: a LinkedIn post by Victor Aarão, available only as screenshots.

- "Storing everything is not memory; it's a log full of noise." The post puts an explicit **memory formation**
  step between the agent's action and persistent memory. That step decides what to extract, consolidate,
  update, invalidate, or forget.
- Some memories have **relation, time and evidence**, e.g. Client → Meeting → Evidence → Decision → Action →
  Outcome. It asks for three things:
  - if something changes, what was true before;
  - for a decision, what evidence was available at that moment;
  - for anything that enters the agent's context, where it came from.
- Vector search stays. Graphs help "when the relation between memories is itself memory". The memory fabric
  in the post has working, episodic, semantic and procedural memory, with a graph (Neo4j) for relations, time,
  evidence and decisions.

**How it maps onto memhub.**

| Post asks for | memhub v1 | v1.1 |
|---|---|---|
| Extract | done | — |
| Consolidate | merge | — |
| Update | supersede | + the judge's `updates` verdict |
| Invalidate | conflict review | — |
| Forget | missing | expiry via `valid_until`: stale, not deleted |
| Evidence | quote + message id | + message timestamp |
| What was true before | version chain | + validity window |
| Relations | `entities`, `conflicts_with` | `links` column; `derived_from` claim → context Episode |
| Where context came from | ids in `<memory>` | `memory_id@version` in the trace metadata |

A graph database is still not adopted. At the pilot's size, and with at most one hop, a jsonb column is enough.

### What the pilot data showed (run 3)

Run 3: 94 threads, 132 candidates, 70 memories (43 facts, 28 episodes).

1. **No usable time.** Every row's `created_at` is the ingestion date, and the evidence has no message
   timestamp. "Next year" cannot be resolved.
2. **Temporary needs stored forever.** Examples: "Is in Grenoble and looking for a dentist accepting new
   patients urgently", "Is looking for housing in Grenoble".
3. **The same idea stored as a Fact and as an Episode**, with no link between them (users 3357f5a1, 7436e250,
   517bec8e, 3fdce811). Reconcile only compares memories of the same type.
4. **Inferred claims look the same as stated ones.** "Is Brazilian and lives in Grenoble" was taken from "Qual é
   o melhor restaurante segundo os brasileiros de Grenoble?".
5. **The prompt erased time on purpose.** It rewrote "deciding whether to apply now or next year" into
   "possibly a newcomer to Grenoble next year".

### The motivating example

"I'm 18 and will apply to Grenoble next year", said on 2026-03-10, holds two claims with different time
behaviour:
- **Age.** Rewrite it into its stable form: born around 2008, as of that date.
- **Plan.** It has a window that ends 2027-12-31 and an open status. After the window it is stale, meaning
  the outcome is unknown. It is not deleted.

Both link to the Episode that holds the situation they were said in. That is the context in the post's cycle.
(Superseded in v1.2: see §11. The Episode turned out to be low value in Habitantes.)

## 11. Run 5 and Claude's own memory: less is more

**Date:** 2026-09-25.

**Run 5 (31 memories, 14 users).** Of the 31, 14 were the automatic context Episodes ("Pergunta sobre X").
Jev rejected all 14 and a reading of the sample agreed. What was useful was small: a few durable facts per user.
Several "facts" were really profile attributes (nationality, city, studies), and a joke question was stored as a
fact because nothing required it to belong to a topic.

**Claude's memory as a model** (screenshots from the user's own Claude memory settings; an example, not a
source of truth). It is a handful of pages in three groups: **You** (Preferences, Profile), **Topics** and
**Areas**. Each page has a title, a one-line summary, a last-updated date and dated details. The lesson the user
drew: reduce the information and the number of items to manage.

**Decisions this led to (v1.2).**
- Profile and Preference become keyed slots (one active row per key), like a profile document, so the same
  identity fact cannot be stored eight times.
- Facts belong to an area, and a fact that fits no area is dropped. Areas are rows linked with `in_area`, with a
  seed list, a cap, and a duplicate check for areas the model proposes. The page is a view over the rows, so
  evidence, expiry and review per claim survive.
- The summary of a page is derived text, never evidence.
- The automatic context Episode is removed; an Episode is a real case (situation/symptom, action, outcome).
- A glossary (Terms) is created only from explicit definitions, because a wrong alias would pollute every
  search. Habitantes users name the same document in Portuguese, French and abbreviations (CNH, carteira de
  motorista, permis de conduire), and other domains are full of synonyms too. Discovering terms from
  co-occurrence is deferred.
- `Plan` is removed. Jev agreed with 1 of 5 plans in run 5, and the useful part of a plan is the durable fact
  behind it, which Profile or a Fact already holds.
- Priorities: for Habitantes the core is Profile, Preference and Facts in areas; Episodes and Terms stay on as
  secondary kinds with smaller caps.
- Inferred claims are dropped by default, to avoid inventing memories.

**Risks noted.** A cheap extractor may pick areas inconsistently, and several Habitantes areas overlap (visa and
documents, university and student permit). Areas are therefore a boost and never a filter, and the area
assignment should be checked with Jev's Choice primitive before it is trusted.
