# Inline review example

An agent proposes a memory and the person in the chat approves, edits or rejects it before anything is saved. It
is built only on memhub's public API (`Memory.check_fields`, `Memory.propose`) and LangChain's human-in-the-loop
middleware. memhub ships the primitives; how an app reviews them is the app's choice, and this is one way.

| File | What it is |
|---|---|
| [agent.py](agent.py) | `build_agent(memory, model, ...)`: the `propose_memory` tool, the review middleware, the `memory_proposed` stream event; `python agent.py` runs a terminal demo |

What happens to a proposal:

| The reviewer answers | Result |
|---|---|
| approve | saved `active` and verified, `reviewed_by` = the reviewer (a candidate instead when the reviewer may not approve that scope) |
| edit, valid | the edited memory is saved as above |
| edit, not valid | nothing saved; the same call is asked again, with the error |
| reject | nothing saved |

The other review flow, "capture now, review later", needs no example: the agent's `propose_memory` from
`Memory.tools()` or `Memory.middleware()` writes a candidate, a correction passes the pending candidate's
`memory_id`, and a UI lists `Memory.queue()` and calls `Memory.approve(id, fields=...)` or `Memory.reject(id)`.

## Run it

Needs `langchain>=1.4.6` (in the `dev` extra) and a chat model; the demo uses `openai:gpt-4.1-mini`.

```bash
uv pip install -e ".[dev,openai]"
memhub init --db sqlite:///review.db
python examples/review_inline/agent.py
```

The test that drives it with a scripted model is `tests/test_example_review_inline.py`.
