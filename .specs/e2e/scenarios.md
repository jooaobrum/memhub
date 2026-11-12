# End-to-end customer scenarios (2026-11-12)

Each scenario starts from nothing: a new folder next to the repo (`../memhub-e2e/<scenario>`), a new venv, only
`OPENAI_API_KEY` in a `.env`, no `~/.config/memhub`, a new database. memhub is installed the way a customer would
(from git; after the fixes, from this working tree). Real OpenAI models (`gpt-4.1-mini`, `text-embedding-3-small`),
real Claude Code (`claude -p`) for the MCP scenarios.

| # | Scenario | Before fixes | After fixes |
|---|---|---|---|
| S1 | Claude Code memory from scratch (ingest past sessions + MCP) | blocked: wrong package, init prompt, 100 % ingest failure | pass |
| S2 | A project configured via the CLI on Postgres, own JSONL logs | pass with friction (blind dry-run) | pass |
| S3 | Python only: `Memory(...)` in an app, LangChain agent, toolkit | crash (`middleware`), lost facts, runaway crash | pass |
| S4 | Birds-species-only memory: own types, closed areas, own prompt | crash on every extraction (closed areas) | pass |
| S5 | Claude Code with MCP only, no ingest | pass, but broken by `memhub use` (B11) | pass |
| S6 | Field-service technicians sharing fixes (`maintenance` preset, `/remember`) | run after the fixes only | pass |

## Bugs found and fixed

| ID | Found in | Symptom (what the customer sees) | Root cause | Fix |
|---|---|---|---|---|
| B1 | S1 | `pip install "memhub[mcp,sqlite]"` (docs/claude-code.md, docs/sqlite.md) installs **another project** (shibing624/memhub 0.2.3, with `memhub-hook`/`memhub-mcp` scripts) | the PyPI name `memhub` is taken | docs install from git; README warns; `uv tool install` for Claude Code |
| B2 | S1 | `memhub init --db … --embeddings … --chat … --preset …` asks `Project (table prefix)` and aborts under a coding agent / CI | `--project` was still prompted when other flags were given | flags mean non-interactive: missing `--project` = folder name, `--preset` = `assistant`; missing `--db`/`--embeddings` stops with the exact command to run. Bare `memhub init` still asks |
| B3 | S1 | **Every** ingest/push with OpenAI fails: `400 Invalid schema for response_format 'Extraction': 'oneOf' is not permitted` | extraction schema was a pydantic *discriminated* union → JSON Schema `oneOf` | plain `Union` (→ `anyOf`); each variant pins `type` as a Literal so validation is unchanged (`pipeline/extract.py`) |
| B4 | S1 | ingest exits 0 with `failed_segments: 2` and ~100 lines of traceback per segment | failures were logged with `exc_info` and never reflected in the exit code | one-line warning (traceback at DEBUG), distinct messages in `errors`, `memhub ingest` exits 1 when a segment failed |
| B5 | S1 | `threads_seen: 2, threads_processed: 0`, no explanation | sessions newer than `segment_idle` (1 h) wait, silently | `threads_waiting` in the summary + a stderr hint naming `ingestion.segment_idle` |
| B6 | S1 | `memhub list` prints 1536 floats per row | CLI printed raw rows | CLI output leaves out `embedding` |
| B7 | S1, S3 | the default `assistant` preset files an English coding chat under **"Trabalho & Estágio"** in Portuguese, with visa/CAF/Grenoble areas and Portuguese examples | the preset was a copy of the Habitantes pilot | generic English preset: 9 general areas (work, tools & tech, home, health, …), domain-neutral rules and examples. Habitantes keeps its own example config. (A `code_style` key added here at first made pytest and type hints compete for one slot and lost one: removed, the model names its own keys) |
| B8 | S3 | "Travels to Berlin for work every month" dropped as `no_area` in a travel app | `assistant` preset had `fact.area: required` (a domain filter) | `fact.area: optional` in the generic preset; `required` stays available for domain configs (S4 uses it) |
| B9 | S3 | app crashes with `openai.LengthFinishReasonError` (32,768 output tokens); slow runs | a runaway generation is not a parse error, so `invoke_structured` let it escape | a cut-off answer is treated like truncated JSON: logged and sampled again (`pipeline/llm.py`) |
| B10 | S3 | `Memory.middleware(workspace_id=…, user_from=lambda ctx: ctx.user_id)` raises `user_id, run_id are required` | the ids were validated before the resolvers were considered | a `*_from` resolver stands in for its id; no run_id needed for a middleware |
| B11 | S2 + S5 | after `memhub use globex` for CLI work, Claude Code sessions in **every** project search `globex` and find nothing | `memhub mcp` read the CLI's global `memhub use` context ahead of its folder | `memhub mcp` reads only `--ws/--user`, `MEMHUB_WORKSPACE/USER`, then the folder / OS user |
| B12 | S1/S5 | a config in `~/.config/memhub` with its key in `.env` next to it fails when Claude Code starts `memhub mcp` in a project folder (no `OPENAI_API_KEY`) | `.env` was only looked up from the current folder upward | a `.env` next to the memhub.yaml in use is loaded too (never over a set variable) |
| B13 | S4 | **every** extraction crashes with `AttributeError: 'ClosedAreaRef' object has no attribute 'new'` when `areas.open: false` | grounding and area planning read `.new`, which the closed ref lacks | `ClosedAreaRef.new` is always `None` |
| B14 | S2 | `ingest --dry-run` shows counts only: nothing to review | dry-run discarded what it computed | `preview`: each memory that would be written (outcome, type, status, content, owner) and each drop with its reason |
| B15 | S1 | `memhub search "testing framework"` → `[]` with no hint, though the memory exists | no `--ws/--user`; Claude Code memories live under the project's workspace | stderr hint naming the workspace/user searched and how to set them |
| B16 | S2 | support preset stored the customer's raw words ("…I read on mobile") as the preference | no voice rule in the preset | third-person rule added (with an off-domain example after the model copied the first one verbatim) |
| B17 | S1 rerun | after S2's `memhub use globex --user globex-anna`, `memhub list` in the S1 project prints `[]` | `memhub use` is one file for the whole machine and silently narrows every CLI command | each command says on stderr when it applies the `memhub use` context (`using workspace=globex user=globex-anna from memhub use (pass --ws/--user to override)`) |
| D1 | S4 | removing a preset's types (`fact: null`) was only documented in a code docstring | — | README documents `<type>: null` |
| D2 | S3 | SQLite in a Google Drive folder: `attempt to write a readonly database` | the sync client takes the file mid-write (environment, not memhub) | docs/sqlite.md warns; suggests `~/.memhub/` |
| D3 | docs | `memhub ingest` without `--source` in docs/sqlite.md fails (the option is required) | — | docs fixed |

## S1: Claude Code memory from scratch

**Customer**: "I want my Claude Code sessions to remember me, in another folder, from zero."

Steps: `uv venv`; install with `[openai,mcp,sqlite]`; two real `claude -p` sessions in `acme-api/` (type hints, pytest,
Fly.io, Postgres 16 staging, structlog, "I'm Joana, tech lead"); `memhub init --db sqlite:///~/.memhub/claude.db …`;
narrow the source to `*memhub-e2e*` (the default would send all 154 local sessions to OpenAI); `ingest`; `search`;
`claude mcp add --scope project memhub -e MEMHUB_CONFIG=… -- <venv>/bin/memhub mcp`; new `claude -p` session asks about
its preferences.

Before: B1 (wrong package), B2 (init aborted), B5 (0 processed, no reason), B3 (2/2 segments failed with HTTP 400, exit
0, B4), then B6 and B7 (memories in Portuguese under "Trabalho & Estágio").

After: init writes the yaml with no question; ingest: 2 threads, 5 created, 3 merged, 0 failed; the new Claude session
calls `search_memory` and reports pytest, type hints, Fly.io, Postgres 16, tech lead Joana.

Final from-scratch rerun on the finished code (`memhub init --db … --embeddings … --chat …`, no other flag): 7 created,
0 failed, all in English: profile "Is the tech lead named Joana.", preferences `logging` (structlog, never print),
`type_hints`, pytest over unittest, facts Postgres 16 staging and Fly.io; areas "Tools & Tech" and "Work & Projects".

Note for the customer: `memhub` in a venv is not on Claude Code's PATH (use the absolute path or `uv tool install`),
and a yaml in some folder is not found from the project folder (use `~/.config/memhub/` or `MEMHUB_CONFIG`). Both
are now in docs/claude-code.md.

## S2: a project via the CLI (Postgres)

**Customer**: "We have a support app. I want memhub configured from the terminal, on our Postgres, fed by our logs."

Steps: own `pgvector/pgvector:pg16` container on port 5455 (a pip install brings no compose file); README Quickstart
(`init --preset support … ${PGPASSWORD}`, `add`, `search`, `doctor`); `config set models.chat`; `config set
sources.jsonl` mapping the app's own field names (`conv`, `customer`, `company`, `q`, `a`); `ingest --dry-run`; ingest;
`queue`/`approve --note`; `use globex --user globex-anna`; `edit` + `history`; `search`; `runs`.

Before: everything ran; `--dry-run` gave counts only (B14), and the preference was stored as the raw quote (B16).
`memhub use` then broke S5 (B11).

After: dry-run `preview` lists `account` "Globex - plan Enterprise, contact Anna" (workspace, candidate) and a
third-person preference (user, active); approve → active with `reviewed_by: cli:<user>`; edit → v2 verified, v1 superseded.

## S3: configured from the Python package

**Customer**: "A travel assistant. No yaml, no CLI: memory configured in code, used by our LangChain agent."

Steps: `Memory(db="sqlite:///travel.db", llm=ChatOpenAI(...), embedder=OpenAIEmbeddings(...), config={"project":
"travel", "preset": "assistant"})`, `init()`, `scope(...)`, `add("text")`, `add(messages, infer=True)`, `search`;
then `create_agent(..., middleware=[m.middleware(workspace_id=..., user_from=...)])` and `m.tools(...)`.

Before: the Berlin fact dropped (B8); one run crashed on a runaway generation (B9); the middleware could not be built
(B10). (A first run also hit D2.)

After (3 runs in a row, clean): profile "Is vegetarian and lives in Porto.", fact "Travels to Berlin for work every
month."; the agent answers "For your next work trip in Berlin, try Kopps, a vegetarian restaurant" with no tool call,
from the injected memory; the toolkit's `search_memory` returns the same rows.

## S4: birds species only (custom types, areas, prompt)

**Customer**: "A birdwatching club app. Memory must hold bird species knowledge and sightings, nothing about people."

Config (`memhub init` then appended to memhub.yaml):

```yaml
types:
  fact: null          # the preset's types are removed: only birds remain
  profile: null
  preference: null
  episode: null
  case: null
  skill: null
  species_note:
    fields: {common_name: str, scientific_name: "str?", note: str}   # with descriptions in the real file
    content_template: "{common_name}: {note}"
    area: required    # an area from the closed list below, or it is dropped
    scopes: [user]
  sighting:
    fields: {species: str, location: str, seen_on: "str?", count: "int?"}
    content_template: "Saw {species} at {location} on {seen_on} (count {count})"
    area: required
    scopes: [user]
areas:
  open: false         # the model may not invent areas
  seeds: [raptors, owls, waterbirds, shorebirds_seabirds, songbirds, corvids, other_birds]   # with titles/descriptions
extraction:
  instructions: "This memory is ONLY about bird species ... Nothing else is ever stored ..."
admission:
  threshold: 0.6      # `memhub config set admission.threshold 0.6`: the preset's 0.75 dropped a behaviour note
```

Input: one chat mixing a night shift as a nurse, bad coffee, two Hoopoes at the Camargue, the Hoopoe's crest and
call, a Grey Heron fishing, moving to Lyon, an iPhone, a binocular question, and a falcon fact said only by the assistant.

Before: every extraction crashed (B13).

After: written: sighting Eurasian Hoopoe (Camargue, 2026-11-12, 2), species_note Hoopoe (crest, "oop-oop-oop" call),
sighting Grey Heron (canal). Not stored: the job, the move, the phone, the coffee, the binocular question, the
assistant-only falcon fact. Areas: Hoopoe → Other birds, Heron → Waterbirds.

Tuning a customer does: remove the preset types with `null`, close the areas, write the instructions, then adjust
`admission.threshold` by reading the `low_score` drops that `add(..., infer=True)` returns.

## S5: Claude Code with MCP only (no ingest)

**Customer**: "I don't want to ingest anything. Just connect memhub and let new sessions save and use memories."

Steps: fresh venv; `memhub init --db … --embeddings …` (no chat model); `claude mcp add --scope project`; session 1:
"remember: ruff for linting, black forbidden, CI on GitHub Actions"; `memhub queue`; `memhub approve` ×2; session 2
asks which linter / where CI runs; session 3 writes a CONTRIBUTING section without being told about memory.

Result: session 1 proposed a `preference` (key `linting`) and a `fact`, both candidates with the user's quote; after
approval session 2 answers from memory; session 3 uses ruff and GitHub Actions on its own. After running `memhub use
globex` (S2), session queries returned `[]` (B11); fixed: MCP stays on its folder's workspace.

## S6: field-service technicians sharing fixes

**Customer**: "Our HVAC technicians chat with an assistant on site. A fix one of them finds should help the others,
but only after a supervisor checks it."

Steps: `Memory(db="sqlite:///~/.memhub/field.db", ..., config={"project": "field", "preset": "maintenance"})`;
technician `tech-marco` pushes a job chat ending with `/remember this fix`; supervisor `sup-dana`
(`roles=["workspace_admin"]`) reads `queue()` and calls `approve(id, note=...)`; technician `tech-lena` searches.

Result: one `incident` "Carrier 50XC rooftop unit #3 fault E47: Replaced clogged liquid-line filter drier", PENDING
(workspace scope waits for review); after approval Lena's search for "low pressure lockout E47" returns it, active.
No bug found.

## Rerun: project-defined memory (2026-11-12)

Feature `project-memory` (issue #18): presets removed, each project's types, areas and prompt in its own `memory.md`.
Same set-up as above: a new folder per scenario (`../memhub-e2e-r2/<scenario>`), a new venv with memhub installed
from this working tree (`uv pip install --reinstall-package memhub --no-cache`: without it uv reused a cached build
of 0.1.0 and the first S4 attempt ran the old code), new databases, real `gpt-4.1-mini` and
`text-embedding-3-small`, real `claude -p`. Every memory file used is in [memory/](memory/).

| # | memory.md | Result | Bugs found (fix) |
|---|---|---|---|
| S1 | memory/starter.md (init starter) | pass | R1 starter body too thin (1cdfdaa), R2 init no longer wrote `scopes: [user]` (6eb1327) |
| S2 | memory/s2-support.md (guide 04) | pass | R3 guide 04 lost the support rules: raw quote stored as the preference (B16 again), `fact` not shared (1cdfdaa); R4 guide 04 runs `init --memory` after AD-035 (6eb1327) |
| S3 | memory/starter.md (named in code) | pass | R1 (1cdfdaa); R5 guide 03 names a memory file for a code-only app (87dfa8d) |
| S4 | memory/s4-birds.md (guide 06) | pass | none |
| S5 | memory/starter.md (init starter) | pass | R2 (6eb1327) |
| S6 | memory/s6-maintenance.md (guide 05) | pass | none |

Bugs found and fixed in this rerun:

- **R1** (S3, then S1/S5): the starter `memory.md` had a two-line body. S3 merged "vegetarian, lives in Porto,
  travels to Berlin" into one profile row written "The user travels...". The removed `assistant` preset carried a
  tuned prompt. Fix (AD-034): `init` writes those domain-neutral rules into the starter body.
- **R2** (S1): without the `assistant` preset `init` no longer wrote `scopes: [user]`; 3 of 5 coding facts became
  workspace candidates waiting for review. Fix (AD-035): `init` writes `scopes: [user]` with the starter.
- **R3** (S2): guide 04's `memory.md` dropped the support preset's third-person rule and the workspace scope of
  `fact`; the preference was stored as the customer's raw words and a solved webhook incident was kept.
- **R4/R5**: guides 04 and 03 follow from R2 and R1.

### S1

Two real sessions in `acme-api/`, then `memhub init` (starter), `ingest --source claude_code` (5 created, 3 dropped
`ungrounded`), all active, user scope: profile "Joana is the tech lead of acme-api.", preferences "In this project
every code uses type hints, and tests use pytest, never unittest." and "Logging must use structlog, never print.",
facts "Deployment is done on Fly.io." and "Staging environment runs Postgres 16.". A new `claude -p` session told to
use only memhub's `search_memory` quotes all five.

### S2

`init --memory ./memory.md` on Postgres; dry-run preview: `account` "Globex - plan Enterprise, contact Anna"
(workspace, candidate) and a third-person preference "Wants answers in short bullet points to facilitate reading on
mobile." (user, active); ingest wrote the same; approve -> active, `reviewed_by: cli:<user>`; edit -> v2 verified, v1
superseded; `config show` names `memory.md`. Model variance: one of five extractions (the first real ingest) missed
the preference; three repeated fresh ingests all wrote both.

### S3

`Memory(..., config={"project": "travel", "memory": "./memory.md"})` with the starter; three runs in a row, each:
profile "Is vegetarian.", profile "Lives in Porto.", fact "Travels to Berlin for work every month." (three memories,
where the old preset gave two); the agent answers "Kopps, a top-rated vegetarian restaurant" in Berlin from the
injected memory.

### S4

Written: sighting "Saw Eurasian Hoopoe at Camargue salt flats on 2026-11-12 (count 2)", species_note "Eurasian
Hoopoe: Raises its crest when it lands; call is a soft 'oop-oop-oop'.", sighting "Saw Grey Heron at canal on
2026-11-12 (count 1)". Hoopoe -> Other birds, Heron -> Waterbirds.

Not stored: the job (nurse), the coffee, the move to Lyon, the phone, the binocular question, the falcon fact said
only by the assistant. No candidate was even proposed for them. 12 frontmatter lines, no `null`, no threshold
(the default 0.5 kept the heron note that the preset's 0.75 dropped).

### S5

Fresh project folder: session 1 proposed a preference (key `python-linter`) and a fact, both candidates with the
user's quote; `memhub approve` x2; session 2, told to use only memhub, quotes both; session 3 writes CONTRIBUTING.md
with ruff and GitHub Actions. Note: Claude Code now keeps its own per-project memory notes. In the `shop-web` folder
reused from an earlier run, session 1 found its own notes and proposed nothing to memhub, so the clean run uses a
new folder, and session 2 is asked to use memhub only.

### S6

`config={"project": "field", "scopes": ["workspace"], "entity_types": ["machine"], "memory": "./memory.md"}`:
`incident` PENDING (+ MERGE of the `/remember` repeat), approved, then Lena's search returns it active. As before.

## Still open / not changed

- `memhub ingest --source claude_code` with no `projects:` reads every local session (154 here) with no confirmation
  or cost estimate. Now documented, with `--dry-run` first; a confirmation prompt is a product decision.
- `cost_usd` is always `null` (no pricing table, by design in v1).
- An extraction slice of two passes can report `ADD` + `MERGE` of the same memory in one `AddResult` (AD-020 behaviour).
- `Memory.search(top_k=)` versus the CLI/service `k`: different names for the same thing.
- A project folder with its own `memhub.yaml` (like S2) makes Claude Code's `memhub mcp` use that config there,
  because `./memhub.yaml` comes first in the lookup.
- `pipeline/ground.py` raises `KeyError` on a candidate whose type the project does not list. A real model cannot
  produce one (the structured-output schema lists the types); found with a fake model in the PR 9 e2e test.
- `memhub edit ""` (an empty or malformed id) prints a traceback instead of an error line.
- Claude Code's own per-project memory can answer before memhub does; a scenario that checks memhub must ask for it.
