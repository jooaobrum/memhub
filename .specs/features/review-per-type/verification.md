# Review per type verification

**Verdict**: FAIL
**Mutations**: 3 - probed C2, C27, C22
**Proofs**: `proofs.md` at 9da53e1
**Verifier**: independent sub-agent (author != verifier)

## Findings

| Finding | Evidence | Fix | Closed |
| --- | --- | --- | --- |
| Under `{builtin: true, <options>}`, the built-in's own options can win over the written ones and no test fails (AC 26: "with those options over the built-in's own") | mutation `{**_short_type(name, "builtin"), **options}` -> `{**options, **_short_type(name, "builtin")}` (`config.py:301`) not killed by `builtin_mapping`: C27 only writes `review` and `retrieval`, which `BUILTIN_OPTIONS` for `profile` / `preference` never set, so the order is never exercised. The code is right at HEAD (own example: `fact: {builtin: true, type_prior: 0.3}` -> 0.3, `case: {builtin: true, extract: true}` -> true) | in `test_builtin_mapping` add an option the built-in sets itself, e.g. `fact: {builtin: true, type_prior: 0.3}` -> `type_prior == 0.3` (built-in 0.6), or `case: {builtin: true, extract: true}` -> `extract is True` | `a97b7bd` - C27 asserts `fact: {builtin: true, type_prior: 0.3}` gives 0.3 |
| C30 never checks the source column of the project `review` line when it is set, nor the source of an unset type's line that inherits a set project value (AC 29: "whose source is where it was set") | `test_config_show_prints_rule` checks `review workspace default` and type lines with a type-level `review` only; `review_rows` takes the project source from `source_of("review")`, and nothing asserts it is anything but `default`. Own example passes at HEAD: project `review: all` in memhub.yaml -> `review all yaml`, `review.fact all yaml`; `review: none` in memory.md -> `review none memory.md`, `review.fact none memory.md` | add a case with the project `review` set in memory.md (and one in memhub.yaml) and assert both the `review` line and an unset type's `review.<type>` line carry that source | `a97b7bd` - C30 asserts `review none memory.md` / `review all yaml` and the inherited `review.fact` lines |
| `{template: "builtin"}` is read as `builtin`, not as a template | `_mapped_type` passes `spec["template"]` to `_short_type`, which treats the text `builtin` as the built-in form: `Config.model_validate({"types": {"fact": {"template": "builtin"}}})` resolves `fact` to `{class: memhub.types:Fact, type_prior: 0.6}`; for a non-built-in name the error is "builtin is not a built-in type", not the template error | in `_mapped_type`, reject a `template` with no `{` before `_short_type` ("`template` is a string with {fields}"), and add it to the C29 table | `a97b7bd` - `_mapped_type` rejects a template with no `{`; C29 case `{template: "builtin"}` |
