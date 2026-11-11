# Configure once: CLI and code (PR 2 of #1) checks

Mutations: 5
Build: `.venv/bin/python -m compileall -q src/memhub`
Plan: `.specs/features/config-and-cli/plan.md`

60 checks in 8 slices · 5 one-way doors · 0 open

Check Cn proves criterion n of the plan (same number).

## Checks

### S1 - Layers, discovery and aliases

**C1** - Each key comes from the highest layer that sets it: `reconcile.duplicate` is 0.92 by default, 0.9 with yaml, 0.95 with env `MEMHUB_RECONCILE__DUPLICATE`, 0.97 with `overrides`; a preset value loses to the yaml (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_layers_last_wins_over_each_lower_layer`

**C2** - yaml `ttl: {temporary: 7d}` keeps `ongoing` at 365 days; yaml `scopes: [user]` replaces the preset's `[user, workspace]` whole (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_blocks_merge_per_key_scalars_and_lists_replace`

**C3** - With `./memhub.yaml`, `$MEMHUB_CONFIG` and a home yaml present the current folder's is read and no other; with the env one and home it is the env one; with only home, home (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_discovery_takes_first_existing_place`

**C4** - With no yaml anywhere `Memory(db=, embedder=)` has the PR 1 defaults (prefix `memhub`, 8 built-in types) (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_no_yaml_anywhere_gives_defaults`

**C5** - `$MEMHUB_CONFIG` naming a missing file raises `ConfigError` containing that path, even when a home yaml exists (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_memhub_config_env_pointing_nowhere_is_error`

**C6** - `config=<path>` reads only that file (missing: `ConfigError` with the path); `config=<dict>` and `config=<Config>` read no file; `MEMHUB_PROJECT=b` loses to `config={"project": "a"}` (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_config_path_dict_and_object_skip_discovery`

**C7** - `MEMHUB_PRESET=support`, `MEMHUB_DB`, `MEMHUB_INGESTION__MAX_CANDIDATES=5` (int), `MEMHUB_SCOPES='[user, workspace]'` (list) set `preset`, `database_url`, `ingestion.max_candidates`, `scopes` (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_env_keys_map_to_nested_yaml_values`

**C8** - `MEMHUB_CONFIG`, `MEMHUB_CLI_ROLES`, `MEMHUB_WORKSPACE`, `MEMHUB_USER` and `MEMHUB_FOO` change nothing in the resolved config (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_reserved_and_unknown_env_vars_are_not_config`

**C9** - `MEMHUB_TYPES__FACT__TYPE_PRIOR=0.9` raises `ConfigError` naming that variable (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_env_var_on_types_is_error`

**C10** - yaml `database_url: A` with env `MEMHUB_DB=B` resolves `B`; yaml with both `project` and `project_prefix` raises `ConfigError` naming both (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_project_and_db_aliases`

**C11** - `examples/habitantes`, `examples/maintenance`, `examples/support` and `memhub.example.yaml` load with the expected `project_prefix`, `types` keys and `sources` keys; `Settings is Config`; `from memhub import Config` works (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_examples_still_load`

**C12** - yaml `project: p` + `preset: assistant` alone loads (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_optional_keys_may_be_absent`

### S2 - Presets and `types:`

**C13** - `preset` read from yaml, env and `overrides` loads the packaged file under the yaml, the highest layer naming it; an unknown name raises `ConfigError` listing `assistant, maintenance, support` (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_preset_layer_and_unknown_preset`

**C14** - Over preset `support`, `types: {ticket: {fields: {symptom: str}}}` leaves `ticket` with only `symptom` and keeps `account`; `{account: null}` removes it; `{faq: {...}}` is added (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_types_block_replaces_adds_removes`

**C15** - None of the 3 presets contains any of the 11 project-specific keys, and each with `project: p` builds a registry (AC 15)
Proof: `.venv/bin/python -m pytest tests/test_presets.py::test_presets_hold_no_project_keys_and_build_registry`

**C16** - `maintenance` has `incident` (fields `equipment`, `fault_code`, `fix`, scope `workspace`); `support` has `account`, `preference`, `ticket`, `fact`; `assistant` has `profile`, `preference`, `fact`, `episode`, `case`, `skill` with scopes `[user]`; each carries its example's extraction/ingestion policy (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_presets.py::test_each_preset_defines_its_types_and_policy`

**C17** - The 3 presets are read with `importlib.resources.files("memhub.presets")` and a built wheel lists them (AC 17)
Proof: `.venv/bin/python -m pytest tests/test_presets.py::test_presets_are_package_resources`
Proof: `.venv/bin/python -m pytest tests/test_packaging.py::test_wheel_contains_presets`

### S3 - `connect:`, `models:` and `Memory()`

**C18** - `connect: "pkg.mod:fn"` imports from the yaml's folder, calls `fn()` once with no arguments, and uses the returned `db`, `embedder`, `llm`, `judge` (AC 18)
Proof: `.venv/bin/python -m pytest tests/test_connect.py::test_connect_factory_called_once_with_no_args_and_objects_used`

**C19** - `"nocolon"`, `"os:nothing"`, a non-callable, a non-dict return and `{"db": pool}` without `embedder` each raise `ConfigError` naming `connect`, with zero connections opened (AC 19)
Proof: `.venv/bin/python -m pytest tests/test_connect.py::test_connect_misuse_is_config_error`

**C20** - Without `connect:`, `models.chat` / `models.embeddings` go through `init_chat_model` / `init_embeddings` with the `provider:model` string; `models.judge` defaults to the chat model; the db is the `db:` URL (AC 20)
Proof: `.venv/bin/python -m pytest tests/test_connect.py::test_models_strings_build_through_langchain_inits`

**C21** - A yaml with legacy `llm:` / `embeddings:` and `models:` builds with `build_chat_model` / `build_embeddings`, not the inits (AC 21)
Proof: `.venv/bin/python -m pytest tests/test_connect.py::test_legacy_blocks_win_over_models`

**C22** - No `connect`, `db` or `database_url` raises `ConfigError` naming `connect`, `db` and `MEMHUB_DB`; no embedder source raises one naming `connect`, `models.embeddings`, `embeddings` (AC 22)
Proof: `.venv/bin/python -m pytest tests/test_connect.py::test_missing_db_or_embedder_source_errors`

**C23** - `Memory(db=pool_a)` with `connect:` returning `pool_b` uses `pool_a`; passing `db` and `embedder` never calls `connect` (call count 0); each of `llm` and `judge` passed beats the factory's (AC 23)
Proof: `.venv/bin/python -m pytest tests/test_connect.py::test_objects_passed_beat_connect_and_skip_it`

**C24** - `Memory()` in a folder whose yaml has `connect:` (and again `db:` + `models.embeddings`) runs `init`, `add`, `search` and finds the added memory (AC 24)
Proof: `.venv/bin/python -m pytest tests/test_connect.py::test_memory_without_arguments_works_end_to_end`

**C25** - `Memory()` with no yaml and no `MEMHUB_DB` raises `ConfigError` listing the 3 discovery places (AC 25)
Proof: `.venv/bin/python -m pytest tests/test_connect.py::test_memory_without_arguments_and_no_yaml_lists_places`

**C26** - A pool from `connect:` has `closed is False` and answers `SELECT 1` after `Memory` calls and after a CLI command (AC 26)
Proof: `.venv/bin/python -m pytest tests/test_connect.py::test_connect_db_left_open`

**C27** - `Memory(..., overrides={"preset": "maintenance"})` has the `incident` type (AC 27)
Proof: `.venv/bin/python -m pytest tests/test_connect.py::test_memory_accepts_overrides`

### S4 - `memhub init`

**C28** - `init --project P --db URL --embeddings prov:model --preset support` with no yaml writes `./memhub.yaml` with `project`, `db`, `models.embeddings`, `preset`, `scopes` `[user, workspace]`, creates both tables, prints `initialized <P>_memory / <P>_memory_runs`, exits 0; the file loads with `project_prefix == P` (AC 28)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_with_flags_writes_yaml_and_tables`

**C29** - `--db` with user and password `memhub` in the URL is written unchanged, exit 0 (AC 29)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_accepts_literal_url_password`

**C30** - `--connect pkg.mod:fn` writes `connect:` and no `db` or `models` key; `--connect` with `--db` exits 1 naming both and writes nothing (AC 30)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_connect_excludes_db_and_models`

**C31** - `--chat prov:model` writes `models.chat`; without it the key is absent (AC 31)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_chat_flag`

**C32** - With stdin input and a missing value it prompts for project (default: folder name as identifier), db or connect, embeddings (not with `--connect`), chat (may be empty), preset (default `assistant`) and never re-asks a value given by flag (AC 32)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_prompts_for_missing_values`

**C33** - init runs validate, build embedder, embed one text, create tables, write yaml, in that order (recorded call order) (AC 33)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_order_probe_before_tables_before_yaml`

**C34** - A failing probe embed, an unreachable db and a probe-dims versus stored-dims `EmbeddingMismatch` each exit 1 with the message and leave no `memhub.yaml` (AC 34)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_failure_writes_no_yaml`

**C35** - `--preset nope` and `--project "1bad-name"` exit 1 before any connection, naming the presets or the identifier rule (AC 35)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_rejects_bad_preset_or_project`

**C36** - An existing `./memhub.yaml` plus a flag exits 1 and the file is unchanged; with `--force` run twice, tables stay untouched and the yaml is rewritten (AC 36)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_existing_yaml_needs_force`

**C37** - `init -c FILE`, and `init` with no flags and a yaml found, create the tables and leave the file byte-for-byte unchanged; no flags and no yaml prompts (AC 37)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_with_c_or_no_flags_keeps_old_behavior`

### S5 - `config show`, `config set`, `save` and secrets

**C38** - `config show` prints `KEY  VALUE  SOURCE` lines: `project  minds  yaml`, `preset  support  env`, `ingestion.max_candidates  5  preset`; `--all` also prints `terms.max_aliases  5  default`, and without `--all` that line is absent (AC 38)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_show_lines_and_sources`

**C39** - `config show` prints `***` for a `password`, `api_key`, `token` or `secret` key and for a URL's password (AC 39)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_show_masks_secrets`

**C40** - `config set ingestion.max_candidates 5` writes int 5; `config set scopes "[user]"` writes a list; other keys and `${VAR}` references stay as written (AC 40)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_set_writes_validated_value_keeps_refs`

**C41** - `config set ingestion.max_candidates abc` exits 1 naming the key with the file byte-identical; with no yaml it exits 1 saying `memhub init` (AC 41)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_set_invalid_or_missing_yaml`

**C42** - `config set models.api_key sk-123` exits 1 naming `${VAR}`, file unchanged, for each of `password`, `api_key`, `token`, `secret`; `'${OPENAI_API_KEY}'` is written (AC 42)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_set_refuses_literal_secret`

**C43** - `Config.save(path)` writes a yaml that loads to an equal `Config`, and a value loaded from `${K}` is written as `${K}` (AC 43)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_save_round_trips_and_keeps_var_refs`

**C44** - `save` with `params={"api_key": "sk-1"}` raises `ConfigError` naming the key path and the target file does not exist afterwards, for each of the 4 key names (AC 44)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_save_refuses_literal_secret_and_writes_nothing`

### S6 - `memhub doctor`

**C45** - `doctor` prints one `ok|warn|fail|skip  <check>  <detail>` line for each of `config`, `db`, `chat`, `embedder`, `dims`, `schema`; exit 0 with no `fail`, exit 1 with one (AC 45)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_prints_checks_and_exit_code`

**C46** - With an unreachable db: `fail db`, the dependent checks `skip`, exit 1 (AC 46)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_db_down_skips_dependents`

**C47** - An embedder answering with 16 dims against a ledger stored with 8 prints `ok embedder` then `fail dims` naming 16, 8 and `memhub reembed` (AC 47)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_dims_mismatch`

**C48** - A configured chat model is invoked once and reported `ok` (or `fail` when it raises); with none configured the line is `skip chat` and the exit code stays 0 (AC 48)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_chat_check_and_none_configured`

**C49** - With a ledger table or the embedding meta missing, `fail schema` names `memhub init`; on an initialised ledger `ok schema` (AC 49)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_schema_missing`

**C50** - A `connect:` pool with `max_lifetime` None, 0, 3600 prints `warn pool` naming the value and exits 0; 3000 prints no pool line; a URL or single connection prints no pool line (AC 50)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_pool_lifetime_warning`

### S7 - `memhub use`, context and `-c` optional

**C51** - `use acme --user anna` writes `{"workspace": "acme", "user": "anna"}` to `~/.config/memhub/context` (dir created), prints `workspace=acme user=anna`; without `--user` the file has `"user": null` (AC 51)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_use_writes_context`

**C52** - With context `acme`/`anna`, env `MEMHUB_USER=bob` and no flags the user is `bob` and the workspace `acme`; `--user carol` gives `carol`; with no context and no env the workspace is the yaml's `workspace_default`; holds for each of `add`, `list`, `search`, `queue`, `areas`, `page` (AC 52)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_context_precedence_flag_env_file_default`

**C53** - `memhub delete` with no id and no `--user` under a context user `anna` exits 1 with "pass either an id or --user" and erases nothing (AC 53)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_delete_user_never_from_context`

**C54** - A context file holding `not json` makes a command exit 1 naming the file (AC 54)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_bad_context_file_is_error`

**C55** - Every registered command except `init` runs with no `-c` against a discovered yaml, honours `-c FILE` over discovery, and with no yaml exits 1 naming the three places and `memhub init` (AC 55)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_c_optional_discovery_and_no_yaml_message`

**C56** - `ingest` with a discovered yaml in another folder resolves a source's relative `path` against that folder (AC 56)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_discovered_yaml_folder_resolves_source_paths`

**C57** - A yaml lacking `database_url` / `db` / `connect` or `embeddings` / `models.embeddings` makes the CLI exit non-zero naming the key (AC 57)
Proof: `.venv/bin/python -m pytest tests/test_cli.py::test_cli_without_database_url_names_key`
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_cli_without_embedder_source_names_key`

### S8 - Quickstart, end to end, nothing breaks

**C58** - The full suite passes (598 existing before this PR) (AC 58)
Proof: `.venv/bin/python -m pytest -q`

**C59** - The README Quickstart contains `memhub init --preset`, `memhub add`, `memhub search` and `Memory()`, and every `memhub <command>` in it answers `--help` with exit 0 (AC 59)
Proof: `.venv/bin/python -m pytest tests/test_packaging.py::test_readme_quickstart_commands`

**C60** - `init --preset support --project <p> --connect tests.e2e.pr2_app:build` in an empty folder, then `add` and `search` with no `-c`, then `Memory()` on the same folder returns the memory `add` wrote (AC 60)
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr2_init.py::test_pr2_demo`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| config layers (5) | defaults · preset · yaml · env · code, C1 table-driven over all 5 | - |
| yaml discovery places (3) | current folder C3 · `$MEMHUB_CONFIG` C3 · home C3 (none: C4, env missing: C5) | - |
| yaml entry points sharing one resolution (6) | CLI commands C55 · `Memory()` C24 · `init` C37 · `config show` C38 · `config set` C40 · `doctor` C45 | - |
| env forms (4) | top-level C7 · nested `__` C7 · alias `DB` C7 · list value C7 | - |
| env names that are not config (5) | `CONFIG` C8 · `CLI_ROLES` C8 · `WORKSPACE` C8 · `USER` C8 · unknown `FOO` C8 | - |
| `types:` operations (3) | replace C14 · add C14 · remove (`null`) C14 | - |
| presets (3) | `maintenance` C16 · `support` C16 · `assistant` C16 | - |
| forbidden preset keys (11) | C15 table-driven over all 11 | - |
| `connect:` misuse (5) | bad shape C19 · missing function C19 · not callable C19 · non-dict C19 · no `embedder` C19 | - |
| `connect:` returned entries (4) | `db` C18 · `embedder` C18 · `llm` C18 · `judge` C18 | - |
| objects passed to `Memory` (4) | `db` C23 · `embedder` C23 · `llm` C23 · `judge` C23 | - |
| `init` inputs (6) | `--project` C28 · `--db` C28 · `--embeddings` C28 · `--preset` C28 · `--chat` C31 · `--connect` C30 | - |
| `init` failure causes (3) | probe embed fails C34 · db unreachable C34 · dims mismatch C34 | - |
| `init` invalid input (2) | unknown preset C35 · bad project identifier C35 | - |
| secret-named keys (4) x writers | `password`, `api_key`, `token`, `secret`: show C39 · set C42 · save C44, each table-driven over the 4 | - |
| doctor checks (6) | config C45 · db C46 · chat C48 · embedder C47 · dims C47 · schema C49 | - |
| doctor pool cases (5) | None C50 · 0 C50 · 3600 C50 · 3000 (no warning) C50 · URL or connection (no line) C50 | - |
| context sources (4) | flag C52 · env C52 · context file C52 · `workspace_default` C52 | - |
| commands that read the context (6) | `add` · `list` · `search` · `queue` · `areas` · `page`, C52 table-driven over all 6 | - |
| commands that must not read it (1) | `delete --user` C53 | - |
| example yamls that stay valid (4) | `habitantes` C11 · `maintenance` C11 · `support` C11 · `memhub.example.yaml` C11 | - |
| one-way doors (5) | yaml keys C10, C28 · `connect:` contract C18, C19 · env and discovery C3, C7, C8 · context file C51, C54 · presets C15, C17 | - |
| `Surface` (0) | None - no route; command exit codes are C28-C57 | - |

## Swept

- validation: C9, C19, C35, C41, C42
- failure modes: C34, C46 (a failed `init` writes no yaml; a failed `config set` leaves the file)
- idempotency: C36 (`init --force` twice leaves the tables)
- authorization: existing - CLI roles from `MEMHUB_CLI_ROLES` (`test_cli.py::test_non_admin_cannot_approve`); C53 (context never widens `delete --user`)
- concurrency: n/a - settings resolution is one process reading files; ledger concurrency is unchanged (PR 1 C14-C16)
- data lifecycle: C40, C43 (what is written comes back unchanged, `${VAR}` kept), C51
- dependency failure: C34, C46, C48 (db, embedder and chat model each fail without a traceback)
- state transitions: n/a - no entity moves through states in this change
- observability: C38, C45 (`config show` and `doctor` are the way to see state)

## Handoff

- **Settled mid-build:** user: "Ok, lets implement. Use the stacked PR from our last branch." then "it should be from this one 1-memhub-as-a-pluggable-memory-package -> origin/1-memhub-as-a-pluggable-memory-package" - branch `feat/config-and-cli` is stacked on `da0aa17`, the head of that branch
- **Dispatched:** none
- **Boundary:** none yet
- **Abandoned:** none
- **Walked:** n/a - no screen
