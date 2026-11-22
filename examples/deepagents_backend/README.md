# deepagents backend example

A deepagents agent keeps its memory as files under `/memories/`. `MemhubBackend` puts memhub under that route: the
agent keeps using `ls`, `read_file`, `grep`, `glob`, `write_file` and `edit_file`. memhub decides what becomes an
active memory. The files are a view rebuilt from the database on every call and are never stored.

```python
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from memhub import Memory
from memhub_backend import MemhubBackend

memory = Memory()  # your memhub.yaml: db, models, memory.md
backend = CompositeBackend(
    default=StateBackend(),
    routes={"/memories/": MemhubBackend(memory, workspace_from=lambda ctx: ctx["team"],
                                        user_from=lambda ctx: ctx["user"])},
)
agent = create_deep_agent(model="openai:gpt-4.1-mini", backend=backend)
agent.invoke({"messages": [...]}, context={"team": "acme", "user": "anna"})
```

| File | What it is |
|---|---|
| [memhub_backend.py](memhub_backend.py) | `MemhubBackend`: deepagents' backend protocol (0.7.x) over `Memory.render` and `Memory.write_page`, public `memhub` names only |

## What the agent sees

```
/memories/
├── users/<user_id>/      the caller's own memory      (user_folder="users/{user_id}")
│   ├── README.md         how to read a page, and the areas with their counts
│   ├── travel.md         one page per area
│   └── travel/flights.md one subject of a large area
└── team/<workspace_id>/  the workspace's memory        (team_folder="team/{workspace_id}")
```

Both folder paths are settings. An agent sees only its own user folder and its own workspace. Any other path
answers `not found`, the same as a path that does not exist.

A page:

```markdown
# Memory: Travel (anna)
<!-- memhub · rendered 2026-11-22T13:26:04Z · stamp 4be0c2a1f9d3 · read-only view, rebuilt from the database -->

## How to read this
- Each line is one memory: `#<id> [provenance] text (date)`. ...

## Memory
#3f9a21c0 [said] "I always fly from CDG, never ORY" (2026-10-02)
#81d00e7a [reviewed] Prefers aisle seats on flights over 3h. (2026-10-14)
#s_flights (12 memories) flights: ... → travel/flights.md

## Pending review (yours, not usable yet)
- [pending] Window seat on short flights. Proposed 2026-11-22.
```

## What a write does

The written page is compared with the current one, line by line, by `#<id>`:

| Change | Becomes |
|---|---|
| a new line with no id | a new memory, if a recent user message says it in so many words (one model call checks it) |
| a line whose text changed, id kept | a new version of that memory, with the same check |
| a line removed | the memory archived |
| header, how-to-read text, summaries, pending section | nothing |
| an id changed or invented | refused |
| a line with no supporting user quote | a **flag** for a reviewer, never a memory |

Whether a change is saved at once or waits in `memhub queue` comes from the project's `review` config, in both
folders, the same as for extracted memories. With the default `review: workspace`, personal lines are saved and
team lines wait. A whole-file `write_file` based on a page that has changed since the agent read it is refused,
so the agent reads it again. `edit_file` applies to the current page.

The tool result lists what happened to each line:

```
Updated file /memories/team/acme/pumps.md
pending review: Pumps fail above 40°C
flag: Team uses Amex for travel (no supporting quote)
refused: line #deadbeef id changed
```

## Run the test

```bash
uv pip install -e ".[dev]"     # deepagents is in the dev extra, and in the `deepagents` extra
pytest tests/test_example_deepagents.py
```
