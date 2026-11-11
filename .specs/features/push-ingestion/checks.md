# Push ingestion - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub`
Plan: `.specs/features/push-ingestion/plan.md`

17 checks in 4 slices · 3 one-way doors · 0 open

## Checks

### S1 - Messages in, memories out

**C1** - A push whose extraction yields a user fact, a workspace memory of a reviewed type and a candidate whose quote is not in the messages returns `AddResult(run_id=<caller's>)` with `ADD`/`active`/`user`, `PENDING`/`candidate`/`workspace`, and `dropped == [Dropped(content=<the candidate's content>, reason="ungrounded")]`; a conversation the extractor finds nothing in returns `results == []` (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_push_returns_events_and_drops`

**C2** - Pushed against stored memories: the same fact again is `MERGE` with the stored `id` and version 1 kept; a new value of a keyed slot is `UPDATE` with `version` 2, or `PENDING` / `candidate` / version 2 when the new version of a reviewed workspace type waits for review; a contradiction of a verified memory is `CONFLICT`, `candidate`, `conflicts_with` = that memory's id (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_events_follow_the_reconcile_decision`
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_a_new_version_waiting_for_review_is_pending`

**C3** - Every row a push writes has the caller's `run_id` and `agent_id` (`None` when not given) and `created_by == "extractor"`; each evidence entry has `source == "push"`, the thread id and the message's id; no `embedding` key in the result's `model_dump()` (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_rows_carry_the_call_ids_and_push_evidence`

**C4** - With `segment_max_user_turns=2`, 5 user turns give 3 run rows covering 2, 2 and 1 user turns, each its own unit of work, written for messages timestamped one second ago and none with `final_pass` (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_segments_by_user_turns_without_idle_wait`

**C5** - A user message `/remember I am allergic to peanuts` stores the memory with `created_by == "remember"` even when the admission threshold would drop it (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_remember_command_in_pushed_messages`

**C6** - `add("text")` and `add({...})` with no `infer` store one memory and make 0 model calls (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_infer_defaults_to_false`

### S2 - The same messages twice

**C7** - Pushing the same 4 messages twice: the second call makes 0 model calls, writes no memory row and no run row, and returns `results == []`, `dropped == []` (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_same_messages_twice_cost_nothing`

**C8** - After [m1..m4]: [m1..m6] extracts m5..m6; the window [m3..m6] extracts m5..m6; [m1, m2] then [m3, m4] extracts m3..m4; after [hi, Noted, Lyon, Noted] with no timestamps, the window [Lyon, Noted, welder, Noted] extracts welder, Noted - each read from the new run row's `first_message_id` / `last_message_id` and the messages the extractor saw (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_only_messages_after_the_watermark`

**C9** - `message_id` is the door 2b formula: "yes" twice in one call -> two ids; the same text from another user or another thread -> another id; with and without `timestamp` -> two ids; equal inputs -> equal ids; `thread_id` defaults to `run_id` (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_message_ids`
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_thread_defaults_to_the_run`

### S3 - Calls that cannot run

**C10** - With `infer=True`: no `llm` raises `ValidationError` naming `llm`; a missing `workspace_id`, `user_id` or `run_id` raises `ValidationError` with 0 model calls and 0 rows (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_invalid_calls_raise_before_any_work`

**C11** - With `infer=True`: `[]`, `[{"role": "user"}]`, `[{"role": "user", "content": 3}]`, `[{"role": "assistant", "content": "hi"}]`, `"x"` with `type="fact"`, and a dict all raise `ValidationError`; `[{"role": "system", ...}, {"role": "user", "content": "hi"}]` runs on the user message only; `add([...])` without `infer` raises `ValidationError` naming `infer=True` (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_message_validation`

**C12** - When the embedder fails on the second of two segments: the call raises that error, the first segment's memory and run row stay, the second leaves none, and the next push of the same messages processes only the second (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_failed_segment_raises_and_is_retried`

**C13** - While another holder has the thread's lock on a backend with `parallel_ingest`, `add(..., infer=True)` raises `Conflict` naming the thread with 0 model calls; on a backend without it, `thread_lock` is never called (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_thread_lock`

**C14** - An unparseable model answer returns `dropped == [Dropped(content=None, reason="extract_error")]`, no exception, and a run row with status `extract_error` (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_extract_error_is_a_drop`

### S4 - The demo

**C15** - A 6-turn conversation: user memories come back `ADD`/`active` and `search` finds them; the workspace memory is `PENDING`, in `queue()`, not in another user's `search`; after a `workspace_admin` approves it another user's `search` returns it - on the in-memory backend, SQLite and Postgres (AC 15)
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr5_push.py::test_push_a_conversation_then_review`

**C16** - `README.md` and `docs/integration.md` show `add(messages, infer=True)`, its `AddResult`, and that `infer` defaults to `False` (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_push.py::test_push_docs`

**C17** - The full suite stays green (AC 16, N1)
Proof: `.venv/bin/python -m pytest -q tests`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| Landing doors (3) | call and result C1 C2 C11 C14 · message ids C9 · source name and run id C3 C8 | - |
| events (5) | `ADD` C1 · `PENDING` C1 · `UPDATE` C2 · `MERGE` C2 · `CONFLICT` C2 | - |
| AC 1 examples (4) | user fact C1 · workspace memory C1 · ungrounded C1 · nothing to keep C1 | - |
| AC 8 examples (3) | superset C8 · sliding window C8 · new turns only C8 | - |
| AC 9 examples (3) | repeated text C9 · other user / thread C9 · with and without timestamp C9 | - |
| AC 11 examples (4) | empty list C11 · no content C11 · system message left out C11 · list without `infer` C11 | - |
| invalid-call guards (3) | no llm C10 · missing ids C10 · bad messages C11 | - |
| backends for the demo (3) | in-memory C15 · SQLite C15 · Postgres C15 | - |
| lock (2) | with `parallel_ingest` C13 · without C13 | - |
| startup config: push entry points (1 assembly) | `Memory.add` C1 C15 | - |

## Swept

- validation: C10, C11
- failure modes: C12 (segment rollback, exception to the caller), C14 (model answer unreadable)
- idempotency: C7, C8 (watermark per thread), C9 (content-hash ids)
- authorization: C15 (another user's `search`, admin approval); existing - `MemoryService` scope rules unchanged
- concurrency: C13 (thread lock)
- data lifecycle: C3 (what is stored), existing - `delete_all` erases push rows like any user row (`test_memory_api.py`)
- dependency failure: C12 (embedder down)
- state transitions: C2 (merge, supersede, conflict), C15 (candidate -> active by approval)
- observability: C4 (run rows per segment, visible in `memhub runs`), C14 (`extract_error` run status)

## Handoff

- **Settled mid-build:** user: "Any question you need from me, I'll let you decide the best decision" - `infer` defaults to `False` (AD-019); `MERGE` appears in results and the result is an `AddResult` (AD-020)
- **Dispatched:** Verifier at `60bac4d`, diff `d75b205..60bac4d` (no screens sub-agent: no capture)
- **Boundary:** C1-C17 closed at `60bac4d`; PR 1's `test_infer_true_not_implemented` removed with the claim it proved
- **Abandoned:** none
- **Walked:** n/a - no screen
