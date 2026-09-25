# memhub — Architecture

Diagrams are Mermaid (render on GitHub, VS Code, most doc sites). Rules and field-level detail live in [tdd.md](tdd.md); adoption steps in [integration.md](integration.md).

## At a glance

For a non-technical audience (management, slides, posts). Agents forget between conversations, and memory tools that write on their own can store things that are not true. memhub keeps a small, trustworthy memory: every item is backed by a real quote, dated, versioned, and approved by a person when it is shared.

![memhub overview](img/memhub-overview.png)

<sub>Source: `img/memhub-overview.html` (arrows are computed from box positions, and the render checks for overflow and overlap). Regenerate: `python img/render.py`.</sub>

**Four guarantees**

| | Guarantee | How |
|---|---|---|
| 🔎 | **Evidence first** | Each memory carries the user's own words; a mechanical check rejects anything without one |
| 🕒 | **Time-aware** | Memories are dated by when they were said, can expire, and newer facts replace older ones with history kept |
| 👤 | **Human in the loop** | Shared knowledge and contradictions wait for approval; a person's edit is never overwritten by the AI |
| 🎯 | **Small on purpose** | Caps, one value per attribute and strict filters keep memory short and useful, not a dump of everything said |

Works with any agent framework, any LLM provider, and a single Postgres database.

## 1. Context

memhub sits between an agent's conversation logs and the agent's prompt. It is a library plus CLI, not a service: there is no daemon and no HTTP API. Postgres is the only stateful component.

```mermaid
flowchart LR
    subgraph Host["Host project (any agent)"]
        Agent["Agent runtime<br/>(LangChain create_agent, or custom)"]
        Logs[("Traces<br/>JSONL files / MLflow")]
    end

    subgraph memhub["memhub package"]
        CLI["CLI<br/>(typer)"]
        Ingest["Ingest pipeline<br/>(batch)"]
        Svc["MemoryService<br/>(public API)"]
        MW["MemoryMiddleware<br/>(LangChain)"]
    end

    PG[("Postgres + pgvector<br/>&lt;p&gt;_memory<br/>&lt;p&gt;_memory_runs<br/>&lt;p&gt;_memhub_meta")]
    LLM["Chat LLMs<br/>extractor + judge"]
    EMB["Embedding model"]
    Human["Reviewer / admin"]

    Agent -- writes --> Logs
    Logs -- read via SourceAdapter --> Ingest
    Ingest --> LLM
    Ingest --> EMB
    Ingest -- one txn per segment --> PG
    Agent <--> MW
    MW --> Svc
    Svc --> PG
    Svc --> EMB
    Human --> CLI --> Svc
    CLI --> Ingest
```

Dependency direction is one-way: the host depends on memhub; memhub imports nothing from the host. Everything project-specific enters through `memhub.yaml`, a source adapter, or a registered type class.

## 2. Modules

```mermaid
flowchart TB
    cli["cli.py<br/>commands, actor, wiring"]
    mw["middleware.py<br/>prompt injection + tools"]
    svc["service.py<br/>MemoryService: authz, validation, search, pages"]
    store["store.py<br/>MemoryStore: SQL, lifecycle, vector search"]
    cfg["config.py<br/>Settings, model factories"]
    types["types.py<br/>MemoryBase + registry"]
    inj["injection.py"]
    sig["signals.py"]

    subgraph pipeline["pipeline/"]
        ing["ingest.py<br/>orchestrator"]
        seg["segment.py"]
        pre["prefilter.py"]
        ext["extract.py"]
        grd["ground.py"]
        scr["score.py"]
        are["areas.py"]
        rec["reconcile.py"]
        rte["route.py"]
        sum["summarize.py"]
        rep["repair.py / verify.py<br/>(optional judge checks)"]
        llm["llm.py<br/>invoke_structured + retries"]
    end

    subgraph sources["sources/"]
        base["base.py<br/>Interaction, SourceAdapter"]
        jsonl["jsonl.py"]
        mlf["mlflow.py"]
    end

    cli --> svc
    cli --> ing
    mw --> svc
    svc --> store
    svc --> inj
    ing --> seg & pre & ext & grd & scr & are & rec & rte & sum & rep & sig
    ing --> store
    ext & rec & sum & rep --> llm
    ing -. reads .-> base
    jsonl & mlf -. implement .-> base
    svc & ing --> cfg
    cfg --> types
    grd --> inj
```

Layering rules:

- `store.py` is the only module that writes SQL. Every method takes an open cursor, so callers group writes into one transaction.
- The ingest pipeline talks to `MemoryStore` directly (it needs several writes per segment in one transaction). Everything else goes through `MemoryService`, which adds authorization, validation, injection scan and embedding.
- `config.py` builds LangChain chat and embedding objects; no provider SDK is imported anywhere else.

## 3. Write path

```mermaid
flowchart TD
    S[("Source adapter<br/>Interaction stream")] --> G["group by thread_id, sort by time"]
    G --> SEG{"new msgs after watermark<br/>and idle ≥ segment_idle?"}
    SEG -- no --> WAIT["wait for next run"]
    SEG -- yes --> SL["slice by segment_max_user_turns"]
    G --> FIN{"idle ≥ thread_close<br/>and no final pass yet?"}
    FIN -- yes --> FP["final pass: whole thread,<br/>Episode-like types only"]
    SL --> PF
    FP --> PF
    PF{"prefilter<br/>min_user_turns / skip_when"} -- skip --> RUN
    PF -- keep --> EX["extract<br/>1 structured LLM call<br/>0..max_candidates"]
    EX --> GR["ground (rules)<br/>quote, role, key, area, injection, inferred, …"]
    GR --> SL2["one value per slot<br/>per segment"]
    SL2 --> EM["embed content"]
    EM --> SC["admission score ≥ threshold"]
    SC --> AR["resolve areas<br/>seed / merge / propose / cap"]
    AR --> RC["reconcile<br/>rules + judge (≤ 1 call)"]
    RC --> RT["route<br/>create · merge · supersede · conflict"]
    RT --> RUN["upsert run row + watermark"]
    RUN --> COMMIT(["COMMIT (one txn per segment)"])
    COMMIT --> SUM["after last segment:<br/>1 judge call per touched area → area summary"]
    SUM --> PURGE["purge dropped older than retention"]

    GR -. dropped + reason .-> RUN
    SC -. low_score .-> RUN
    RC -. outdated / inferred .-> RUN
```

Every dropped candidate ends in `<p>_memory_runs.dropped` with a reason; `memhub runs` aggregates them. This is the main tuning tool (thresholds, prompts, caps).

## 4. Reconcile decision (keyed slot)

The most intricate step. For a slot (e.g. `profile/city`) the active row M is found by key, with no similarity search.

```mermaid
flowchart TD
    C["Candidate C for slot with active row M"] --> A{"same text, or C's messages<br/>already in M's evidence?"}
    A -- yes --> MERGE["merge: add evidence,<br/>renew observed_at / valid_until"]
    A -- no --> P{"same statement already<br/>pending against M?"}
    P -- yes --> MP["merge into pending candidate"]
    P -- no --> O{"C older than M?"}
    O -- yes --> D1["drop: outdated"]
    O -- no --> V{"M verified?"}
    V -- yes --> CF["conflict candidate<br/>(M stays active)"]
    V -- no --> I{"C inferred, M stated?"}
    I -- yes --> D2["drop: inferred"]
    I -- no --> ST{"M stale and key mutable?"}
    ST -- yes --> SUP["supersede: version N+1"]
    ST -- no --> J["judge (1 call)"]
    J -- same --> MERGE
    J -- extends --> EXT["supersede with merged text<br/>(own words only, else conflict)"]
    J -- "updates + mutable" --> SUP
    J -- "updates + immutable /<br/>conflicts / unreadable" --> CF
```

Non-keyed candidates use the same rules after a similarity lookup (`duplicate ≥ 0.92` merges; otherwise the nearest rows above `compare_floor` go to one judge call that picks the row it concerns; `unrelated` creates).

## 5. Read path

```mermaid
sequenceDiagram
    participant U as User
    participant A as Agent
    participant M as MemoryMiddleware
    participant S as MemoryService
    participant DB as Postgres
    participant E as Embedder

    U->>A: message
    A->>M: before_agent(state, runtime.context)
    alt first invocation of state
        M->>S: list(active, type=always types / skills)
        S->>DB: SELECT
        M-->>A: state.memory_snapshot (about_the_user, skill_index)
    end
    M->>S: search(last user message, k)
    S->>DB: active terms (glossary expansion)
    S->>E: embed(expanded query)
    S->>DB: nearest area, vector search (+ area boost)
    S-->>M: rows with areas
    M->>S: page_for_query(query)
    S->>E: embed(query)
    S->>DB: nearest area + linked rows
    M-->>A: state.memory_turn, state.memhub_injected
    A->>M: wrap_model_call(request)
    M-->>A: request with snapshot + turn appended to system message
    A->>U: answer
    Note over M: injected memory_id@version list also written<br/>to the active MLflow trace, if any
```

Search filters: `status='active'`, visible scope (workspace rows of the workspace + the user's own), not stale, not `area` rows. Area match and `related` terms only re-rank; they never filter.

## 6. Data model

```mermaid
erDiagram
    MEMORY ||--o{ MEMORY : "versions (same memory_id, version N..)"
    MEMORY }o--o{ MEMORY : "links: in_area → area row"
    MEMORY }o--o| MEMORY : "conflicts_with (memory_id)"
    MEMORY {
        uuid id PK "version id"
        uuid memory_id "stable across versions"
        int version
        text type "profile|preference|fact|episode|term|area|skill|custom"
        text scope "user|workspace"
        text workspace_id
        text user_id "null for workspace"
        text status "candidate|active|rejected|archived|superseded"
        bool verified
        text content "embedded statement"
        jsonb payload "type-validated"
        vector embedding
        jsonb evidence "quote, message_id, observed_at, claim_source"
        int seen_count
        timestamptz observed_at
        timestamptz valid_until "null = no expiry"
        text durability
        text assertion "stated|inferred"
        jsonb links "kind=in_area"
        uuid conflicts_with
        text created_by "extractor|summarizer|actor"
    }
    RUNS {
        uuid run_id "one CLI invocation"
        text source
        text thread_id
        text last_message_id "watermark"
        timestamptz last_message_at
        bool final_pass
        jsonb signals
        jsonb dropped "candidate + reason"
        text status "ok|extract_error"
        int tokens_in
        int tokens_out
    }
    META {
        text key PK "embedding"
        jsonb value "model, dims"
    }
```

Key invariants, enforced by unique indexes and checked in tests after every scenario step:

1. At most one `active` version per `memory_id`.
2. At most one `active` row per `(owner, keyed type, key)`.
3. Versions are contiguous from 1.
4. Every extractor-created row has at least one quote.
5. Evidence contains no duplicate `message_id`.

Area pages are views over this table, not stored documents:

```mermaid
flowchart LR
    A["area row (type=area)<br/>title, description, summary"]
    F1["fact row<br/>links: in_area → A"]
    F2["fact row<br/>links: in_area → A"]
    E1["episode row<br/>links: in_area → A"]
    F1 --> A
    F2 --> A
    E1 --> A
    A --> Page["page(owner, area)<br/>= area header + linked active,<br/>non-stale rows newest first"]
```

## 7. Memory lifecycle

```mermaid
stateDiagram-v2
    [*] --> candidate: workspace scope, conflict,<br/>propose_memory, add by non-admin
    [*] --> active: user-scope extraction (verified=false),<br/>add by owner/admin (verified=true)
    candidate --> active: approve
    candidate --> rejected: reject
    active --> superseded: edit / supersede (new version N+1 is active)
    active --> archived: archive
    rejected --> [*]: delete
    active --> [*]: delete (own user row) / delete --user
    note right of active
        stale = active AND valid_until < now
        (computed; hidden from search)
    end note
```

## 8. Deployment topologies

memhub has no runtime of its own; choose where its two halves run.

```mermaid
flowchart LR
    subgraph Batch["Batch worker (cron / scheduler)"]
        I["memhub ingest --source X"]
    end
    subgraph Online["Agent service"]
        AG["Agent + MemoryMiddleware<br/>or MemoryService calls"]
    end
    subgraph Ops["Operator machine"]
        R["memhub queue / approve / page / runs"]
    end
    DB[("Postgres + pgvector")]
    LG[("Trace store<br/>JSONL / MLflow")]

    AG -- logs --> LG
    LG --> I
    I --> DB
    AG <--> DB
    R --> DB
```

- **Ingest** runs periodically (it is idempotent and watermark-driven); one process at a time per thread set.
- **Agent side** needs only read access plus the `propose` write path; the middleware acts as a role-less `Actor("agent")`.
- **Ops** uses the CLI with `MEMHUB_CLI_ROLES` for review.
- All three share `memhub.yaml` (or an equivalent `Settings` object) and the same embedding model; a mismatch with what `init` saved is refused at startup.

## 9. Extension points

| Point | Mechanism | Contract |
|---|---|---|
| New trace source | Class with `read()` (+ optional `signals()`, `skipped`) | Yield `Interaction`; call `ingest_source()` |
| New memory type | Subclass `MemoryBase`, register `module:Class` under `types:` | `content` is the embedded statement; bump `schema_version` on shape change |
| Different LLM / embedder | `llm.*` / `embeddings` config | Chat model must support structured output; embedder needs `embed_query` |
| Project policy | `extraction.instructions`, `types.*.keys`, `areas.seeds`, `skip_when` | Config only |
| Slots | `keyed: true` + `keys` | One active row per `(owner, key)`; reconcile handles it with no code |
| Areas | `area: required|optional` on a type | Requires a seed list or `areas.open: true` |

## 10. Design decisions

| Decision | Alternative | Why |
|---|---|---|
| Postgres + pgvector, two data tables + meta | Vector DB + graph DB | One dependency; transactions across memory and watermark; SQL filters on time, scope, status |
| Version rows, not in-place update | Mutable rows + event log | History and `observed_at` semantics for free; only the newest active row is queried |
| Stale computed, not stored | Expiry job | No background process; time-travel tests just pass `now` |
| Relations as a `links` column | Edge table | Only one hop (`in_area`) is needed; GIN answers "what is in this area" |
| Areas as rows, pages as views | Stored documents | Nothing to keep in sync; summary is the only derived text and is labelled |
| Mechanical grounding | LLM judge for support | Cheap, deterministic, zero-hallucination target; judge checks are opt-in extras |
| Judge called ≤ 1× per candidate, unreadable ⇒ conflict | Retry / auto-pick | Bounded cost; a person decides ambiguity; nothing is overwritten silently |
| Batch ingestion | Write-through on each turn | Segments need idle time for context; traces are already durable |
| CLI as the only review UI | Web hub | Scope of v1; the `MemoryService` API is the seam for a future UI |
