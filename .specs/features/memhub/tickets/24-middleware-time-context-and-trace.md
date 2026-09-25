# 24 — Middleware shows time, inference and context, and records what it injected

**What to build:** The agent sees how old each memory is, whether it was inferred, and the situation it came
from. Anything that enters the agent's context can be traced back to the exact memory version, without a new
memhub table. The middleware is still not wired into Habitantes.

- Injected `<memory>` items take the form:
  `[id|type|verified] content (as of <observed_at date>, inferred) — context: <episode content> (source)`.
  "inferred" appears only when `assertion = inferred`, and the context part only when there is one.
- Stale memories are never injected: neither the per-turn search nor the `retrieval: always` profile loaded at
  thread start includes them.
- Retrieval trace:
  - every injected item's `memory_id@version` is appended to agent state under `memhub_injected`;
  - when the host provides a trace (MLflow), the same list is written to that trace's metadata;
  - memhub stores nothing for this.
- `search_memory` tool results use the same format.

**Blocked by:** 20 — Stale memories out of search; 22 — Stated versus inferred; 23 — Context Episode and links

**Status:** done

- [x] Against a fake agent, an injected item shows its "as of" date, the `inferred` label when applicable, and its context Episode
- [x] A stale preference is left out of the thread-start profile, and a stale fact is left out of per-turn injection
- [x] After a turn, agent state holds `memhub_injected` with the `memory_id@version` of every injected item
- [x] With a fake trace, the same list is written to its metadata; with no trace, nothing fails
