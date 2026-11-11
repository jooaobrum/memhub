# config-and-cli verification

**Verdict**: PASS
**Mutations**: 5 - probed C1 (env above code), C14 (types null), C42 (config set secret), C50 (pool bound), C52 (context vs env); 4 killed, 1 not
**Proofs**: `proofs.md` at cbe7b04
**Verifier**: independent sub-agent (author != verifier)

## Findings

| Finding | Evidence | Fix | Closed |
| --- | --- | --- | --- |
| Mutation not killed: `set_key` secret guard narrowed to `SECRET_KEYS[:3]` (`secret` no longer refused by the first guard) and no test fails | `settings.py` `set_key` `segs[-1] in SECRET_KEYS`; the later `check_no_secrets` still raises, so only the message differs | in `test_config_set_refuses_literal_secret` assert the message names `${VAR}` for all four keys, or drop the redundant guard | 70bee5d - C42 now asserts the `pass a ${VAR} reference` message for all four key names |
| `MEMHUB_PROJECT=123` (yaml-parsed to int) escapes as a raw pydantic `ValidationError`, not `ConfigError` | `settings.resolve(environ={"MEMHUB_PROJECT":"123"})`; env values are yaml-parsed in `env_layer` and validated in `_resolve` unwrapped | wrap `Config.model_validate` in `_resolve` into `ConfigError` naming the key (CLI would otherwise show a traceback) | 70bee5d - `test_a_value_the_field_cannot_hold_is_a_config_error_not_a_traceback` (C7) |
| `config set project b` on a file that uses `project_prefix` fails with "sets both `project` and `project_prefix`" | `set_key` maps only canonical -> the file's alias spelling, not alias -> the file's canonical spelling | apply the mapping both ways | 70bee5d - `test_config_set_follows_the_files_spelling_of_an_alias` (C40) |
| `mask` leaks part of a URL password that contains `@` | `mask("db","postgresql://u:p@ss@h/d")` -> `postgresql://u:***@ss@h/d` | greedy match up to the last `@` before the host | 70bee5d - `test_config_show_masks_a_password_that_holds_an_at_sign` (C39) |
| Not broken (probed): `${VAR}` in an unquoted flow mapping, env names with empty segments (`MEMHUB_`, `MEMHUB___X`, `MEMHUB_A____B`), env preset over yaml preset keeps yaml `types` overrides, `config set` with secret mapping or multi-ref value | own examples at HEAD | none | fa9a5ac - not a finding: probed at HEAD and held |
