# hybrid-search proofs

**HEAD**: 0d7d9e4
**Tree**: clean
**Result**: 25 pass, 0 fail; mutations 5 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "whole_token"` | 1 | 1 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "tokenise"` | 1 | 1 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "keyword_hits_keep_filter_and_guard"` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "keyword_hits_keep_a_time_filter"` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "stopwords_four_languages"` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "config_stopwords"` | 1 | 1 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "max_df"` | 3 | 3 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "no_keyword_left"` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "idf_rank"` | 1 | 1 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "hybrid_off_is_today"` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_search_sql.py -k "issue_the_sql_recorded"` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "per_call_override"` | 1 | 1 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "search_defaults"` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "fuse_weights"` | 1 | 1 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "tiers_keep_their_order"` | 1 | 1 | pass |
| C19 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "matched_by_and_similarity"` | 1 | 1 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "invalid_search_config"` | 6 | 6 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "config_show_lists_search_keys"` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "vector_weight_on_a_type_without_keywords"` | 1 | 1 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_hybrid_search.py -k "bench_codes or hybrid_docs"` | 2 | 2 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub scripts` | - | - | pass |

## Mutations

| Mutation | Location | Killed |
| --- | --- | --- |
| max_df boundary <= -> < (a keyword in exactly max_df of the rows dropped) | `src/memhub/storage/rules.py:183` | yes |
| a row in both legs labelled matched_by vector | `src/memhub/storage/rules.py:204` | yes |
| rows scoring 0 kept (vector_weight 0 still returns vector-only rows) | `src/memhub/storage/rules.py:207` | yes |
| Postgres prefilter without accent folding (PRÉFÈRE not found by prefere) | `src/memhub/storage/postgres.py:803` | yes |
| keyword leg run without the tier's Filter | `src/memhub/service.py:417` | yes |
