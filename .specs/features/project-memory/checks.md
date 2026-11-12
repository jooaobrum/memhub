# Project-defined memory - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub`
Plan: `.specs/features/project-memory/plan.md`

37 checks in 7 slices · 4 one-way doors · 0 open

## Checks

### S1 - The memory file is read

**C1** - With `memory: ./memory.md` in `memhub.yaml`, `types`, `areas`, `strict` come from the frontmatter and `extraction.instructions` equals the body stripped of leading and trailing blank lines; resolving through `-c` from a subfolder gives the same types (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_memory_file.py::test_yaml_names_the_file`

**C2** - `MEMHUB_MEMORY=./m.md` and `config={"memory": "./m.md"}` each read the file relative to the working directory (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_memory_file.py::test_env_and_code_name_the_file`

**C3** - A missing file raises `ConfigError` with `memory file not found: <absolute path>` (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_memory_file.py::test_missing_file`

**C4** - A frontmatter key other than `types`, `areas`, `strict` (`db`) raises `ConfigError` with `memory.md: unknown key 'db'; allowed: types, areas, strict` (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_memory_file.py::test_unknown_frontmatter_key`

**C5** - `types`, `areas`, or `extraction.instructions` (with a non-empty body) set in both the naming layer and the file each raise `ConfigError` with `<key> is set in both memhub.yaml and memory.md: keep one`; `extraction.instructions` in the yaml with an empty body loads (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_memory_file.py::test_key_in_both_places`

**C6** - A file with no frontmatter is all body and gets the starter types; an empty body leaves the default instructions (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_memory_file.py::test_no_frontmatter_and_empty_body`

**C7** - `memhub config show` prints source `memory.md` for every key the file sets (`types.*`, `areas.*`, `strict`, `extraction.instructions`) and no `preset` row (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_show_names_the_memory_file`

**C37** - Frontmatter that is not valid YAML raises `ConfigError` naming the file (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_memory_file.py::test_bad_yaml_frontmatter`

### S2 - Short forms

**C8** - A template's placeholders become its fields in order, typed by `:type` (default `str`), optional with `?`; the stored `content_template` holds bare `{name}`; every one of `str int float bool list[str] list[int]` is accepted (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_template_declares_fields`

**C9** - `{n:number}` raises `ConfigError` naming `number`, the field `n` and the allowed types (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_unknown_placeholder_type`

**C10** - A placeholder used twice with different types raises `ConfigError` naming the field; used twice with the same type loads (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_placeholder_twice`

**C11** - Optional sections render as the plan's four examples: `seen_on=None, count=2` → `Saw Heron at canal (count 2)`; both empty → `Saw Heron at canal`; both set → `Saw Heron at canal on 2026-11-12 (count 2)`; `[[x]] {a}` → `[x] A`; a template with no `[` renders as today (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_optional_sections_render`
Proof: `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_written_memory_content_uses_sections`

**C12** - `<name>: builtin` equals today's built-in entry for each of `fact preference profile episode case skill`; `sighting: builtin` raises `ConfigError` listing the six (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_builtin`

**C13** - A string with no placeholder that is not `builtin` raises `ConfigError` with `sighting: a type is a template with {fields}, builtin, or a mapping` (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_plain_string_type`

**C14** - Areas as `key: text` become seeds: `raptors` → `Raptors`, `other_birds` → `Other birds`, `shorebirds_seabirds` → `Shorebirds seabirds`, description kept (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_area_short_form`

**C15** - Each long-form example config resolves to the same project types, areas and instructions as at the base snapshot `tests/fixtures/examples_resolved_base.json`, through the long forms alone (AC 15)
Proof: `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_long_forms_unchanged`

### S3 - strict

**C16** - `strict: true` sets `areas.open` false and `area: required` on every listed type without `area`; a type with `area: optional` keeps it; `strict` absent changes nothing (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_memory_file.py::test_strict_expands`

**C17** - Under `strict: true`, a pushed candidate with no area comes back in `dropped` with reason `no_area` (AC 17)
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr9_memory_file.py::test_strict_drops_off_domain`

**C18** - `strict: true` leaves `admission.threshold` at 0.5 with no other layer, and at 0.7 when the yaml sets 0.7 (AC 18)
Proof: `.venv/bin/python -m pytest tests/test_memory_file.py::test_strict_keeps_threshold`

### S4 - No presets, starter types

**C19** - `preset:` in the yaml, `MEMHUB_PRESET` in env, and `preset` in code each raise `ConfigError` with `preset: is removed: list your memory types in memory.md (see docs/memory.md)` (AC 19)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_preset_is_refused`

**C20** - The built wheel has no path under `memhub/presets`; `memhub init --preset support` exits 2 (AC 20)
Proof: `.venv/bin/python -m pytest tests/test_packaging.py::test_wheel_has_no_presets`
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_preset_flag_is_gone`

**C21** - With no layer listing types the config holds exactly `fact`, `preference`, `profile`, `area`, `term` with today's options, and an in-memory `Memory` with no config adds and searches a `fact` (AC 21)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_starter_types`
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_bare_memory_uses_starter_types`

**C22** - `types: {}` raises `ConfigError` with `memhub.yaml defines no memory types: add types to memory.md (see docs/memory.md)` (AC 22)
Proof: `.venv/bin/python -m pytest tests/test_settings.py::test_empty_types_refused`

**C23** - `Memory.add` with no `type` on a project without `fact` raises `ValidationError` with `type is required: this project has no 'fact' type (types: sighting, species_note)` before any database or model call (AC 23)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_add_without_type_needs_fact`

### S5 - memhub init writes the memory file

**C24** - `memhub init` with flags and no `--memory` prompts nothing, writes `memory: ./memory.md` into `memhub.yaml`, and a `memory.md` listing `fact`, `preference`, `profile` as `builtin` with the default body, which resolves (AC 24)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_flags_writes_memory_file`

**C25** - Interactive init asks `In a sentence, what should this memory keep?` and writes the answer under `# What to remember`; an empty answer gives the default body (AC 25)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_asks_what_to_keep`

**C26** - `init --memory ./mine.md` names that file and writes no `memory.md`; a missing PATH exits 1 with `memory file not found: <path>` (AC 26)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_with_memory_path`

**C27** - An existing `memory.md` is kept byte for byte without `--force`, with `kept memory.md` on stderr; with `--force` it is rewritten (AC 27)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_keeps_memory_file`

### S6 - Examples and onboarding docs

**C28** - `examples/habitantes`, `examples/maintenance`, `examples/support` each have a `memory.md` named by their `memhub.yaml`, and resolve to the same project types, areas and instructions as `tests/fixtures/examples_resolved_base.json` (internal `area`, `term` aside) (AC 28)
Proof: `.venv/bin/python -m pytest tests/test_examples.py::test_examples_match_base`

**C29** - `docs/memory.md` has sections for the file, short forms, long forms, `strict`, starter types and a worked example; `docs/memory-setup.md` asks what to keep, what never to keep, how it groups, writes `memory.md`, and tests it with one pushed conversation (AC 29)
Proof: `.venv/bin/python -m pytest tests/test_docs.py::test_memory_docs_exist`

**C30** - Every fenced block opening with `---` in `README.md`, `docs/` and `examples/` resolves when written to a `memory.md` (AC 30)
Proof: `.venv/bin/python -m pytest tests/test_docs.py::test_memory_blocks_load`

**C31** - `README.md`, `memhub.example.yaml`, `src/`, `docs/` and `examples/` contain no `preset` (case-insensitive) (AC 31)
Proof: `.venv/bin/python -m pytest tests/test_docs.py::test_no_preset_left`

**C32** - `docs/memory.md` and `docs/memory-setup.md` do not contain `Claude` (AC 32)
Proof: `.venv/bin/python -m pytest tests/test_docs.py::test_memory_docs_name_no_vendor`

**C33** - Every `memhub <command> --flag` in the README and `docs/guides/*.md` is accepted by that command's `--help`; the README quickstart runs init → `memory.md` → `add` → `search` in that order; guide 06 holds the short-form birds `memory.md` with 20 frontmatter lines or fewer and no `admission.threshold` (AC 33)
Proof: `.venv/bin/python -m pytest tests/test_docs.py::test_doc_commands_exist`
Proof: `.venv/bin/python -m pytest tests/test_docs.py::test_quickstart_order_and_birds_guide`

### S7 - The e2e scenarios rerun

**C34** - Fake-model birds demo: `memhub init` in an empty SQLite folder, the birds `memory.md`, one mixed conversation pushed; only `sighting` and `species_note` rows are written, every other candidate is in `dropped` with a reason, and the table and column set equals that of an `init` with no memory file (AC 34)
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr9_memory_file.py::test_birds_demo`

**C35** - `.specs/e2e/scenarios.md` has a dated section `## Rerun: project-defined memory` with one row per S1-S6 giving the `memory.md` used (S1, S3, S5 `init starter`; S2 support; S4 birds; S6 maintenance), the result `pass`, and the bugs found with their fix commits; each memory file used is saved under `.specs/e2e/memory/` and resolves (AC 35)
Proof: `.venv/bin/python -m pytest tests/test_e2e_record.py::test_rerun_recorded`

**C36** - The S4 file `.specs/e2e/memory/s4-birds.md` has 20 frontmatter lines or fewer, no `null`, no `admission.threshold`; its recorded run lists the Hoopoe and Heron sightings and the Hoopoe species note as written, and the job, the move, the phone, the coffee, the binocular question and the falcon fact as not stored (AC 36)
Proof: `.venv/bin/python -m pytest tests/test_e2e_record.py::test_s4_birds_recorded`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| Landing doors (4) | door 1 memory file C1 C2 C3 C4 C5 C6 C37 · door 2 short forms C8 C9 C10 C11 C12 C13 C14 · door 3 presets removed C19 C20 · door 4 starter types C21 C22 | - |
| layers that name the memory file (3) | yaml C1 · env C2 · code C2 | - |
| frontmatter keys: 3 allowed + other (4) | `types` C1 · `areas` C1 · `strict` C16 · other C4 | - |
| keys set in both places (3) | `types` C5 · `areas` C5 · `extraction.instructions` C5 | - |
| placeholder types (6) | `str` C8 · `int` C8 · `float` C8 · `bool` C8 · `list[str]` C8 · `list[int]` C8 | - |
| optional sections, AC 11: 4 examples + no-bracket (5) | `seen_on` empty C11 · both empty C11 · both set C11 · `[[x]]` C11 · no `[` C11 | - |
| built-in names: 6 + unknown (7) | `fact` C12 · `preference` C12 · `profile` C12 · `episode` C12 · `case` C12 · `skill` C12 · `sighting: builtin` C12 | - |
| area titles, AC 14 (3 examples) | `raptors` C14 · `other_birds` C14 · `shorebirds_seabirds` C14 | - |
| layers refusing `preset` (3) | yaml C19 · env C19 · code C19 | - |
| config assemblies (3) | `Memory()` C21 · CLI `init`/`config show` C24 C7 · push pipeline C17 C34 | - |
| `memhub init` exits (3) | 0 C24 · 1 C26 · 2 C20 | - |
| init modes (4) | flags C24 · interactive C25 · `--memory` C26 · existing file C27 | - |
| examples (3) | `habitantes` C28 · `maintenance` C28 · `support` C28 | - |
| onboarding docs: README, 7 guide files, 4 docs, 2 new (4 checks) | presence of the new two C29 · memory blocks C30 · no `preset` over all C31 · commands in README and guides C33 | - |
| e2e scenarios (6) | S1 C35 · S2 C35 · S3 C35 · S4 C35 C36 · S5 C35 · S6 C35 | - |

## Swept

- validation: C4, C9, C10, C12, C13, C22, C37
- failure modes: C3, C26 (missing file); C5 (conflicting keys); config errors raise before any database or model call (C23)
- idempotency: C27 (a second `init` keeps the user's file)
- authorization: n/a - the config is read by the process that owns it; no new caller or route
- concurrency: n/a - the memory file is read once per `resolve`; nothing is written concurrently
- data lifecycle: C34 (no table or column added, N5); rows of an unlisted type keep today's behaviour (plan, Changes underneath)
- dependency failure: n/a - no external call; YAML parsing failure is C37
- state transitions: n/a - no entity moves through states
- observability: C7 (`config show` names the file), C27 (`kept memory.md` on stderr)

## Handoff

- **Settled mid-build:** 2026-11-12, after the S3/S2 reruns showed weaker extraction than the presets' tuned prompts - asked "What should `memhub init` write as the starter body?" - user chose "Tuned rules in the body (Recommended)" (AD-034)
- **Dispatched:** none
- **Boundary:** none yet
- **Abandoned:** none
