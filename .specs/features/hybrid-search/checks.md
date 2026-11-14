# Hybrid search - checks

Mutations: 5
Build: `.venv/bin/python -m compileall -q src/memhub scripts`
Plan: `.specs/features/hybrid-search/plan.md`

24 checks in 4 slices · 3 one-way doors · 0 open

Run from the worktree with `PYTHONPATH=src` exported, so the tests import this tree's `src` and not the editable install's. The Postgres legs need Docker (the `pg_dsn` fixture). `CONTRACT` below stands for `tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py`, written out in each proof.

## Checks

### S1 - an exact code or rare word is found, on every backend

**C1** - With hybrid on, of `"fault E47 on the rooftop unit"` and `"fault E74 on the rooftop unit"` (the E74 one nearer by embedding), the query `"E47"` returns the E47 memory first with `matched_by` `both` or `keyword`; `"e47"` does too; `"E74"` returns E74 first; on in-memory, SQLite (vec and fallback) and Postgres (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "hybrid_code_ranks_first"`

**C2** - With hybrid on and `k=5`, a memory naming `PRD_1` that 25 distractors outrank by embedding comes back within `k` for the query `"PRD_1"`, with `matched_by: "keyword"`, on every backend (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "hybrid_id_outside_vector_candidates"`

**C3** - A keyword matches only a whole token: `E47`/`E470` no, `PRD_1`/`PRD_10` no, `P-101`/`P-101.` yes, `6205`/`bearing 6205-2RS` no, `pompe`/`Pompe` yes, `prefere`/`préfère` yes (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "whole_token"`

**C4** - The tokeniser splits on whitespace, on punctuation other than `-_/.` inside a token, and on an apostrophe: `l'usine` → `usine`, `dell'impianto` → `impianto`, `v2.3.1,` → `v2.3.1`, `(E47)` → `e47` (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "tokenise"`

**C5** - `vectors.keyword_search` applies the search rules: no candidate, rejected, archived, stale (unless `include_stale`), `area`, other-workspace or other-user row, and the `type`, `EntityFilter` and `Filter` arguments narrow it as they narrow `vectors.search`, on every backend (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "keyword_search_applies_the_search_rules"`

**C6** - `vectors.keyword_search` returns every searchable row whose casefolded, accent-folded content contains a keyword as a substring (`prefere` finds `PRÉFÈRE`; `e47` finds `E470` at this stage), each with `similarity` equal to its cosine to the query embedding, and the count of rows that pass the WHERE clause, on every backend (door 3)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "keyword_search_prefilter_count_and_similarity"`

**C7** - Through `Memory.search` with hybrid on, a `require_filter` type's tagged memory that names the code is not returned unless the filter names that type, and a subject filter keeps keyword hits to that subject (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "keyword_hits_keep_filter_and_guard"`

**C23** - Through `Memory.search` with hybrid on and `vector_weight 0`, a filter that is not about subjects (`observed_at` from 2026-10-01) leaves an out-of-range memory naming the code out of the keyword leg (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "keyword_hits_keep_a_time_filter"`

### S2 - everyday words do not pollute the result

**C8** - Tokens shorter than 2 characters and the built-in it/fr/en/pt stopwords are dropped: `the`, `le`, `de`, `il`, `que`, `do`, `E` dropped; `und`, `E47` kept (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "stopwords_four_languages"`

**C9** - `search.stopwords: ["Máquina"]` drops `maquina` from the query `"maquina E47"`, so the keywords are `[e47]` (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "config_stopwords"`

**C10** - A keyword in more than `keyword_max_df` of the searchable rows is dropped: 10 rows, `max_df 0.5`, in 6 → dropped, in 5 → kept; `max_df 1.0` → never dropped (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "max_df"`

**C11** - When no keyword is left, or no row holds one, `search` returns the rows and order of `hybrid=False`, each with `matched_by: "vector"`, with one memory type and with two (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "no_keyword_left"`

**C12** - Keyword hits rank by the sum of `ln(1 + (N - df + 0.5) / (df + 0.5))` over the distinct keywords they contain; a keyword repeated in one row counts once; ties by similarity (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "idf_rank"`

### S3 - fusion, its weights and its switch

**C13** - With hybrid off, `search` returns today's rows: no `matched_by`, and `vectors.keyword_search` is never called (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "hybrid_off_is_today"`

**C14** - With hybrid off, Postgres `service.search` issues the SQL statements recorded before the ports and before the filter grammar (AD-014) (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_search_sql.py -k "issue_the_sql_recorded"`

**C15** - `Memory.search(hybrid=True|False)` overrides `search.hybrid` for that call; `hybrid="yes"` raises `ValidationError` before any model call (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "per_call_override"`

**C16** - A config with no `search:` block has `hybrid true`, `vector_weight 1.0`, `keyword_weight 1.0`, `keyword_max_df 0.5`, `stopwords []`, and `Memory.search` with no `hybrid` argument searches hybrid (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "search_defaults"`

**C17** - Fusion orders by `vector_weight / (60 + rank_v) + keyword_weight / (60 + rank_k)`, ties by similarity, rows scoring 0 dropped: keyword 1 + vector 5 beats vector 1 only; `keyword_weight 0` → vector order; `vector_weight 0` → keyword hits only, in keyword order (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "fuse_weights"`

**C18** - With hybrid on and two subject tiers, every tier-1 row ranks above every tier-2 row, a keyword-only row included, and each tier's keyword hits are about that tier's subject (AC 15)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "tiers_keep_their_order"`

**C19** - `matched_by` is `both` for a row in both lists, `keyword` for keyword only, `vector` otherwise; `similarity` is the cosine for each with no boost, and a boosted row (area) keeps the boost, the same value as with hybrid off (AD-058); the toolkit and MCP `search_memory` results carry `matched_by`, the middleware's text block does not change (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "matched_by_and_similarity"`

**C20** - Loading a config with `vector_weight: -1`, `keyword_weight: -0.1`, `keyword_max_df: 0`, `keyword_max_df: 1.5`, both weights 0, or `stopwords: "x"` raises `ConfigError` naming the key (AC 17)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "invalid_search_config"`

**C21** - `memhub config show --all` lists `search.hybrid`, `search.vector_weight`, `search.keyword_weight`, `search.keyword_max_df`, `search.stopwords` with their values (AC 18)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "config_show_lists_search_keys"`

**C24** - Merging `fact` and `episode` with `vector_weight 0.2`, an episode search whose keyword `keyword_max_df` dropped ranks its rows by `0.2 / (60 + rank)`: `fact E47 fault` (keyword, 1/61), then `episode E47 0` and `fact other 0` (vector, 0.2/61, by similarity) (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "vector_weight_on_a_type_without_keywords"`

### S4 - measured and documented

**C22** - `scripts/bench_search.py --codes` runs with no database and prints, for each code-style query, the rank of the expected memory with hybrid on and off; `docs/filters.md` has a "Hybrid search" section naming the five keys, their defaults, the four stopword languages and domain stopwords; `docs/sqlite.md` says the keyword leg needs no FTS5 (AC 19, AC 20)
Proof: `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "bench_codes or hybrid_docs"`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| backends (4 legs) | in-memory C1 C2 C5 C6 · SQLite sqlite-vec C1 C2 C5 C6 · SQLite fallback C1 C2 C5 C6 · Postgres C1 C2 C5 C6 | - |
| `Memory.search(..., hybrid=)` statuses (3) | 200 C1 · 422 C15 · 404 C5 | - |
| `memhub.yaml` `search:` statuses (2) | 200 C16 · 422 C20 | - |
| doors (3) | config keys C16 C20 C21 · `matched_by` C19 C11 C13 · `keyword_search` port C5 C6 | - |
| config keys (5) | `hybrid` C16 C15 C13 · `vector_weight` C17 C20 · `keyword_weight` C17 C20 · `keyword_max_df` C10 C20 · `stopwords` C9 C20 | - |
| whole-token rule, AC 3 (6 examples) | `E470` C3 · `PRD_10` C3 · `P-101.` C3 · `6205-2RS` C3 · `Pompe` C3 · `préfère` C3 | - |
| tokeniser, AC 4 (4 examples) | `l'usine` C4 · `dell'impianto` C4 · `v2.3.1,` C4 · `(E47)` C4 | - |
| stopword languages (4) | it C8 · fr C8 · en C8 · pt C8 | - |
| max_df boundary, AC 8 (3 examples) | 6/10 C10 · 5/10 C10 · 1.0 C10 | - |
| fusion examples, AC 14 (4) | equal weights C17 · keyword 0 C17 · vector 0 C17 · type without keyword hits C24 | - |
| `matched_by` values (3) | `both` C19 · `keyword` C19 · `vector` C19 | - |
| search rules on the keyword leg, AC 5 (10) | candidate C5 · rejected C5 · archived C5 · stale C5 · area C5 · other workspace C5 · other user C5 · `EntityFilter` C5 · `Filter` C5 C23 · guard C7 | - |
| invalid config, AC 17 (6) | `vector_weight -1` C20 · `keyword_weight -0.1` C20 · `max_df 0` C20 · `max_df 1.5` C20 · both 0 C20 · `stopwords` not a list C20 | - |
| readers (6) | `Memory.search` C1 C15 · `memhub search` C14 · toolkit `search_memory` C19 · middleware `search_memory` (text unchanged, order hybrid) C19 · MCP `search_memory` C19 · `memhub config show` C21 | - |
| startup config: `search:` (2 assemblies) | `resolve` (yaml, `Memory()`) C16 C20 · `Config(...)` in code (tests, `Memory(config=)`) C16 | - |

## Swept

- validation: C15, C20
- failure modes: n/a - read only; a failure in either leg raises as `vectors.search` does today, nothing is written
- idempotency: n/a - read only
- authorization: C5, C7 (the keyword leg reuses the owner, workspace and guard rules)
- concurrency: n/a - read only, one unit of work per search as today
- data lifecycle: C5 (stale and status rules); no stored data changes
- dependency failure: existing - the embedder is called once as today; the keyword leg calls no external service
- state transitions: n/a - no state changes
- observability: C19 (`matched_by` on every row shows which leg found it), C22 (bench ranks)

## Handoff

- **Settled mid-build:** AC 16 / C19 restated after Verifier finding 4 - asked "Which should hold?" - user chose "Keep boosts (Recommended)" -> AD-058; C19 reworded - asked "What should the middleware show?" - user chose "JSON tools only (Recommended)": toolkit and MCP results gain `matched_by`, the middleware's text stays as it is
- **Dispatched:** Verifier at `2b99ba1` (proofs green, 23/23) - returned, FAIL, 5 findings; no screens sub-agent (no capture binds)
- **Boundary:** none yet
- **Abandoned:** none
