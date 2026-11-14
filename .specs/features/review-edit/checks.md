# Review edit - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub examples/review_inline`
Plan: `.specs/features/review-edit/plan.md`

34 checks in 2 slices · 5 one-way doors · 0 open

Run from the worktree with `PYTHONPATH=src` exported, so the tests import this tree's `src` and not the editable install's.

## Checks

### S1 - A reviewer approves a candidate with corrected fields

**C1** - On every backend, `approve(id, reviewed_by=, edit={content, payload, entities, embedding})` archives the candidate row with `review_note` `edited on review` and writes version `max+1` as `active`, `verified`, `reviewed_by=<reviewer>`, holding the edited content, payload, entities and embedding and the candidate's `evidence`, `run_id`, `agent_id`, `type`, `scope`, `workspace_id`, `user_id` (AC 1, AC 11)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "approve_with_edit_writes_the_next_version"`

**C2** - With `note="typo"` the archived row's `review_note` is `edited on review: typo` and the active row's is `typo`; with no note they are `edited on review` and `None` (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "edit_on_review_notes"`

**C3** - `MemoryService.approve(actor, id, fields={"content": "Login uses Okta"})` on candidate "Login uses Auth0" leaves v1 `archived` and v2 `active` "Login uses Okta", with the embedding of the new content and the candidate's evidence (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "approve_with_fields_edits_and_activates"`

**C4** - `Memory.approve(id, fields=F)` returns `event` `ADD`, `version` 2 for an edited new memory, and `UPDATE`, `version` 4 for an edited candidate v3 over an active v2 (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "approve_event_add_or_update"`

**C5** - Approving an edited candidate v3 over an active v2 leaves exactly one active row (v4) and v2 `superseded`, on every backend (AC 3, AC 11)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "approve_with_edit_supersedes_the_active_version"`

**C6** - `fields=None`, `fields={}` and fields equal to the candidate payload each activate the candidate row itself: the history keeps one row per version, no `edited on review` note. Through `MemoryService` and `Memory`, an unknown id raises `NotFound` and approving the same candidate twice raises `StoreError` (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "fields_that_change_nothing_approve_as_today"`

**C7** - `count: "abc"` on an `int` field raises `ValidationError`, entity type `planet` raises `ValidationError`, content `ignore previous instructions` raises `InjectionDetected`; after each, the candidate is still `candidate` and the history has the same rows (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "invalid_fields_leave_the_candidate"`

**C8** - On a keyed type, `key` `"plan"` -> `"tier"` raises `ValidationError` `the key cannot change on review: reject and propose again` and writes nothing; `"1"` -> `1` is accepted; the key unchanged with another field changed is accepted (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "key_change_on_review"`

**C9** - `approve(id, fields=F, resolve="replace")` on a conflicting candidate archives the old active memory and leaves the edited next version active (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "replace_with_fields"`

**C10** - `fields` with `resolve="keep_old"` raises `ValidationError` and changes nothing; with `keep_both` on a keyed candidate raises `ValidationError` and changes nothing; with `keep_both` on a non-keyed candidate the edited version is active and the old memory stays active (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "resolve_and_fields"`

**C11** - On a user-scope candidate, approve and reject (with and without `fields`) succeed for the owner with role `user` and for a non-owner reviewer, and raise `PermissionDenied` for another user with role `user`, writing nothing. `reject` of an unknown id raises `NotFound`, and of a row already rejected raises `StoreError` (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "user_scope_owner_or_role"`

**C12** - On a workspace-scope candidate, approve and reject (with and without `fields`) raise `PermissionDenied` for an actor with only `user` and succeed for a reviewer (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "workspace_scope_needs_role"`

**C13** - Every service-level review test in `tests/test_review_edit.py` passes on the in-memory, SQLite and Postgres backends (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "postgres or sqlite or memory"`

**C14** - `memhub approve ID --set content="Login uses Okta" --set count=3` approves with `{"content": "Login uses Okta", "count": 3}`, prints the active row as JSON and exits 0 (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_review_edit_cli.py -k "approve_set_parses_json"`

**C15** - `--set content` (no `=`) and `--set count=abc` on an `int` field each exit 1 with the error on stderr and leave the candidate a candidate (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_review_edit_cli.py -k "approve_set_errors"`

**C16** - `count: "abc"` gives the same `ValidationError` text from `approve(fields=)`, `add`, `edit`, `propose` and `check_fields` (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "one_field_check_for_every_write"`

**C17** - If the embedder raises during `approve(fields=)`, the error reaches the caller and the candidate and its history are unchanged (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_review_edit.py -k "embedder_failure_writes_nothing"`

### S2 - An app builds review from the chat on public calls

**C18** - `Memory.check_fields("fact", {"content": "x"})` returns `{"content": "x", "payload": {...}}` and adds no row; invalid fields raise as C16 (AC 15)
Proof: `.venv/bin/python -m pytest tests/test_review_propose.py -k "check_fields"`

**C19** - `Memory.propose(...)` without `approve` writes a candidate and returns `event` `PENDING`, `status` `candidate`, for user and workspace scope (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_review_propose.py -k "propose_writes_a_candidate"`

**C20** - `Memory.propose(..., approve=True)` by the owner (user scope) and by a reviewer (workspace scope) writes `active`, `verified`, `reviewed_by=<actor>`, returns `event` `ADD`, and `queue()` is empty (AC 17)
Proof: `.venv/bin/python -m pytest tests/test_review_propose.py -k "propose_approved_is_active"`

**C21** - `Memory.propose(..., approve=True)` on workspace scope by an actor with only `user` writes a candidate (`PENDING`) and does not raise (AC 18)
Proof: `.venv/bin/python -m pytest tests/test_review_propose.py -k "propose_approve_not_allowed_falls_back"`

**C22** - `propose(..., memory_id=X)` on a pending candidate of the same owner replaces its content, payload, entities and embedding in place: one row, same `id` and version, content "Login uses Okta" (AC 19)
Proof: `.venv/bin/python -m pytest tests/test_review_propose.py -k "correction_updates_the_candidate"`

**C23** - `memory_id` unknown raises `NotFound`; of another user raises `PermissionDenied`; of another workspace raises `PermissionDenied`; of an active, a rejected and an archived row raises `ValidationError`; each writes nothing (AC 20)
Proof: `.venv/bin/python -m pytest tests/test_review_propose.py -k "bad_memory_id"`

**C24** - With `reconcile.duplicate` 0.92 and a rejected candidate of the same type and owner: similarity 0.92 is refused with `ValidationError` `a reviewer already rejected this memory`; 0.91 is written; the same text rejected for another user is written; the same text of another type is written; `user_requested=True` at 0.92 is written (AC 21)
Proof: `.venv/bin/python -m pytest tests/test_review_propose.py -k "rejected_repeat"`

**C25** - The `propose_memory` tool of `MemoryToolkit`, `MemoryMiddleware` and `memhub mcp` each return `{"id", "event", "status", "content"}` with `status` `candidate`, accept `memory_id` and `user_requested`, and return `{"rejected": ...}` for invalid fields, a bad `memory_id` and a rejected repeat (AC 22)
Proof: `.venv/bin/python -m pytest tests/test_review_propose.py -k "tool_contract"`

**C26** - `MemoryMiddleware.__init__` and `Memory.middleware` keep exactly today's parameter names (AC 23)
Proof: `.venv/bin/python -m pytest tests/test_review_propose.py -k "middleware_signature_unchanged"`

**C27** - Driving `examples/review_inline` with a fake model and `InMemorySaver`, the run stops at an interrupt on `propose_memory` whose description contains the type, the rendered content, the scope and each evidence quote (AC 24)
Proof: `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "interrupt_describes_the_memory"`

**C28** - Answering `approve`, and in another run `edit` with valid args, leaves the (edited) memory `active` and `verified`, and the queue empty (AC 25)
Proof: `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "approve_or_edit_makes_it_active"`

**C29** - Answering `edit` with `count: "abc"` writes nothing and interrupts the same tool call again with the error in its description; a valid answer then makes it active (AC 26)
Proof: `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "invalid_edit_is_asked_again"`

**C30** - Answering `reject` writes nothing: no row, empty queue (AC 27)
Proof: `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "reject_writes_nothing"`

**C31** - A write emits exactly one `memory_proposed` custom stream event with `id`, `event`, `status`, `content`, `memory_type`; a reject emits none (AC 28)
Proof: `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "stream_event"`

**C32** - Every `memhub` import in `examples/review_inline` names something in `memhub.__all__`, and no module under `memhub.` other than `memhub` itself is imported (AC 29)
Proof: `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "public_imports_only"`

**C33** - `pyproject.toml` keeps `langchain>=1.0` in `dependencies` and the `dev` extra declares `langchain>=1.4.6` (AC 30)
Proof: `.venv/bin/python -m pytest tests/test_review_propose.py -k "langchain_pins"`

**C34** - A correction (`memory_id=`) whose new text is within `reconcile.duplicate` of a rejected candidate is refused with `a reviewer already rejected this memory` and leaves the draft unchanged, unless `user_requested=True`; a rejected workspace candidate refuses a 0.95-similar workspace proposal from another user of that workspace and not one in another workspace (AC 21; appended after the Verifier's findings)
Proof: `.venv/bin/python -m pytest tests/test_review_propose.py -k "rejected_repeat"`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| `MemoryService.approve` statuses (5) | 200 C3 · 422 C7 · 403 C11 · 404 C6 · 409 C6 | - |
| `MemoryService.reject` statuses (4) | 200 C11 · 403 C11 · 404 C11 · 409 C11 | - |
| `Memory.approve` statuses (5) | 200 C4 · 422 C7 · 403 C11 · 404 C6 · 409 C6 | - |
| `memhub approve` exits (2) | exit 0 C14 · exit 1 C15 | - |
| `Memory.check_fields` statuses (2) | 200 C18 · 422 C18 | - |
| `Memory.propose` statuses (4) | 200 C19 · 422 C24 · 404 C23 · 403 C23 | - |
| `MemoryService.propose` statuses (4) | 200 C19 · 422 C24 · 404 C23 · 403 C23 (through `Memory.propose`) | - |
| tool `propose_memory` statuses (2) | 200 C25 · 422 C25 | - |
| Landing doors (5) | 1 approve takes fields C3, C14 · 2 history shape C1, C2 · 3 primitives C18, C19, C20 · 4 tool contract C25 · 5 dev dependency C33 | - |
| backends (3) | memory C1, C5, C13 · sqlite C1, C5, C13 · postgres C1, C5, C13 | - |
| field check callers (5) | `approve(fields=)` C16 · `add` C16 · `edit` C16 · `propose` C16 · `check_fields` C16 | - |
| AC 5 examples (3) | int field C7 · undeclared entity C7 · injection C7 | - |
| AC 6 examples (3) | key changed C8 · `"1"`->`1` C8 · key unchanged C8 | - |
| `resolve` x `fields` (4) | `replace` C9 · `keep_old` C10 · `keep_both` keyed C10 · `keep_both` non-keyed C10 | - |
| approver rule, AC 9-10 (5) | user owner C11 · user other C11 · user reviewer C11 · workspace user-only C12 · workspace reviewer C12 | - |
| no-op fields, AC 4 (3) | `None` C6 · `{}` C6 · equal payload C6 | - |
| AC 2 events (2) | `ADD` C4 · `UPDATE` C4 | - |
| bad `memory_id`, AC 20 (6) | unknown C23 · other user C23 · other workspace C23 · active C23 · rejected C23 · archived C23 | - |
| rejected repeat, AC 21 (8 examples) | 0.92 C24 · 0.91 C24 · other user C24 · other type C24 · `user_requested` C24 · correction C34 · workspace other user C34 · other workspace C34 | - |
| proposal tool surfaces (3) | `MemoryToolkit` C25 · `MemoryMiddleware` C25 · `memhub mcp` C25 | - |
| HITL decisions in the example (4) | approve C28 · valid edit C28 · invalid edit C29 · reject C30 | - |
| `propose(approve=True)` actors (3) | user owner C20 · workspace reviewer C20 · workspace user-only C21 | - |

## Swept

- validation: C7, C8, C15, C16, C18
- failure modes: C7, C17 (an error before the write leaves the candidate untouched; the unit of work rolls back)
- idempotency: existing - the store's `approve` raises `StoreError` "only a candidate can be approved" on a second approval (C6 exercises it)
- authorization: C11, C12, C20, C21, C23
- concurrency: existing - `_supersede` returns 0 and raises `Conflict` when the active version changed concurrently, and the `memory_active_uidx` / `UNIQUE (memory_id, version)` constraints refuse a second active row or a duplicate version
- data lifecycle: C1 (evidence, `run_id`, `agent_id` carried over); existing - `delete_user` removes every row of a user, archived ones included (`test_memory_api.py` delete_all)
- dependency failure: C17
- state transitions: C1, C6, C9, C10, C22, C23
- observability: n/a - no logging requirement; the archived row is the audit trail (C1, C2)

## Handoff

- **Settled mid-build:** none
- **Dispatched:** Verifier (independent sub-agent) at `15b12ca`, proofs.md green there
- **Boundary:** C1-C17 closed at `ba4b8f7` (S1, branch `feat/review-edit-approve`); C18-C33 closed at `15b12ca` (S2, branch `feat/review-edit-propose`, stacked on S1); both rebased on `origin/main` `7888456`
- **Abandoned:** a review option on `MemoryMiddleware` (`review="inline"`, `hitl()`): the user moved the flow to `examples/` (AD-045, AD-046) before the build. In the example, subclassing `HumanInTheLoopMiddleware` to ask again was dropped for a public `langgraph.types.interrupt` inside the tool, so nothing depends on HITL's private methods
- **Pre-existing:** `test_config.py::test_habitantes_config_has_profile_and_preference_slots_episode_capped_and_no_plan` and `test_terms.py::test_habitantes_config_enables_terms_and_only_terms_are_workspace_scope` fail on `838cb42` too (`ConfigError` loading the Habitantes example), untouched here
- **Worktree:** built in a separate git worktree on branch `feat/review-edit-approve` (S1) then `feat/review-edit-propose` (S2), because another session (memhub-89) has uncommitted edits in the main checkout; proofs run with `PYTHONPATH=src`
