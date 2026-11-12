# Project-memory verification

**Verdict**: FAIL
**Mutations**: 3 - probed C11, C2, C14
**Proofs**: `proofs.md` at d5ddf85
**Verifier**: independent sub-agent (author != verifier)

## Findings

| Finding | Evidence | Fix | Closed |
| --- | --- | --- | --- |
| An optional `[...]` section whose list placeholder is `[]` is rendered, and no test fails | mutation `in (None, "", []) -> in (None, "")` at `src/memhub/types.py:218` not killed by `test_optional_sections_render` | add a C11 case with `{tags:list[str]?}` = `[]` that expects the section left out | 5c4b783 - C11 (`test_optional_sections_render`, empty list case; mutation killed) |
| The memory file named by the lowest layer wins over the highest, and no test fails | mutation `named[-1] -> named[0]` at `src/memhub/settings.py:346` not killed by `tests/test_memory_file.py`; at HEAD, yaml `memory: ./a.md` + `MEMHUB_MEMORY=./b.md` reads `b.md`, but no test holds that | assert that env or code `memory:` overrides the yaml's `memory:` (types and instructions come from the higher layer's file) | 5c4b783 - C2 (`test_env_and_code_name_the_file`, env over yaml; mutation killed) |
| The area title can lowercase the rest of the key, and no test fails. The plan says only the first letter is upper | mutation `text[:1].upper() + text[1:] -> text.capitalize()` at `src/memhub/config.py:139` not killed by `test_area_short_form`; every key the test uses is lowercase | add a key with capitals (`DNA_samples` -> `DNA samples`) to C14 | 5c4b783 - C14 (`test_area_short_form`, `DNA_samples` -> `DNA samples`; mutation killed) |
| An area key that YAML reads as a non-string (`2024:`, `yes:`) crashes with an `AttributeError` instead of a `ConfigError` | own example: `areas: {2024: year list}` in `memory.md` -> `AttributeError: 'int' object has no attribute 'replace'` from `area_title` (`config.py:138`) | turn the key into a `str`, or raise `ConfigError` naming the key; add a test | 5c4b783 - C14 (`test_area_short_form_edges`: a non-text key raises `ConfigError` naming it) |
| An area short form with no description (`raptors:` -> `None`) is dropped without a word | own example: `areas:\n  raptors:\n` resolves with `areas.seeds == []`, no error. `_short_seeds` takes only `str` values, and the extra key is ignored | refuse a non-string short-form value (or treat `None` as an empty description); add a test | 5c4b783 - C14 (`test_area_short_form_edges`: `raptors:` kept with an empty description) |
| An area keyed like an `AreasConfig` setting (`open`, `seeds`, `merge_similarity`, ...) is read as that setting | own example: `areas: {open: open-country birds}` -> `invalid configuration: areas.open: Input should be a valid boolean`, which does not say the key is reserved | name the reserved keys in the error, or document them in `docs/memory.md` | 5c4b783 - C14 (`test_area_short_form_edges`: `areas: \`open\` is a setting of areas, not an area`) |
| C11's proofs do not run the test that holds its `[[x]] {a}` -> `[x] A` and plain-template claims | `proofs.md` C11 runs `::test_optional_sections_render` (3) and `::test_written_memory_content_uses_sections` (1); `test_literal_brackets_and_plain_templates_render` (`tests/test_type_short_forms.py:63`) is not selected | add `::test_literal_brackets_and_plain_templates_render` as a C11 proof | 5c4b783 - C11 proof added: `::test_literal_brackets_and_plain_templates_render` |
| C5's proof does not run the test that holds its "`extraction.instructions` in the yaml with an empty body loads" claim | `proofs.md` C5 runs `::test_key_in_both_places` (3 params); `test_instructions_in_yaml_with_an_empty_body_load` (`tests/test_memory_file.py:94`) is not selected | add `::test_instructions_in_yaml_with_an_empty_body_load` as a C5 proof | 5c4b783 - C5 proof added: `::test_instructions_in_yaml_with_an_empty_body_load` |

Own examples that held at HEAD: `count=0` and `count=False` in an optional section render (not treated as empty); `types:` (null) in `memory.md` raises the `NO_TYPES` error like `types: {}`; `_private` -> `Private`.
