---
# Each type is `builtin`, a template such as "Saw {species} at {location}[ on {seen_on?}]", or a mapping.
# Add `areas:` and `strict: true` to keep only your own domain. See docs/memory.md.
types:
  fact: builtin
  preference: builtin
  profile: builtin
---
# What to remember

Durable facts about the user and their work, what they prefer, and who they are.

## Kinds

- `profile`: who the user is: identity, role, where they live or work, what they study, their team, lasting
  constraints and goals. One free sentence per statement, only what the user said.
- `fact`: other lasting knowledge about the user's situation or environment: their projects, tools, conventions,
  household, commitments.
- `preference`: how the user wants the assistant to answer or to work for them.

Ask of every candidate: will this help me serve this user better in a month? If not, emit nothing.

## Voice

Write `content` in the third person, in the language the user wrote in, never in the first person and without
"The user": "Prefers pytest over unittest", "Lives in Lisbon", not "I prefer pytest". Do not guess the user's gender.
A statement about someone else (a child, partner, colleague, pet) names that person: "Daughter Alice is lactose
intolerant", never "Is lactose intolerant". Jokes and sarcasm are never memories.

## Keep

What the user stated in a declarative sentence and that is still true and useful in a month: role and employer, team,
projects and the stack they use, coding conventions they want followed, where they live, studies, family, pets, health
constraints and diet, habits, budget, language level, decisions already taken, and dated commitments (a trip, a start
date, a deadline) written with the absolute date and `valid_until` set to that date. One memory per distinct fact (up
to 5 per segment), even when the message also asks a question.

## Never

- Questions, how-to requests, searches or recommendation requests ("how do I configure X?", "looking for a good gym"),
  and "The user asked / wants to know / is looking for ...".
- Something deduced from the topic of a question: asking about Paris rents does not mean the user lives in Paris.
- Facts that only appear in the assistant's answers.
- Vague intentions ("I'm thinking of", "maybe I'll"): keep only a durable fact behind them, if any.
- An acute one-off errand: a lost card, a single bug being fixed right now.
- Something too thin to be useful ("Works", "Has a project"): keep it only with the concrete detail.
- Secrets: passwords, keys, tokens.

If in doubt, emit nothing: an empty list is a good answer.

## Time

Every message shows its date, e.g. [#3 2026-07-21]. Never write relative time ("next year", "recently", "soon",
"now") in `content`: rewrite it with the date of the message that says it ("next year" said on 2026-07-21 -> "in
2027"), or drop it. A fact whose text gives an end gets `valid_until` = the last day of that period.

## Preferences

Store one when the user asks for a way of working or answering ("keep answers short", "always use type hints", "no
emoji", "call me Sam"), or praises a format ("perfect, keep it this short"). A request that only concerns the current
answer ("this time give me details") is not a preference. Set `key` to a short snake_case name (`format`, `language`,
`logging`); one row per key, the latest wins. Write `content` as a complete sentence saying what is wanted. A request
covering two things gives two memories.

## Examples

These only illustrate the rules: never emit a memory from them.

- "I'm the tech lead on the payments API, we deploy on Fly.io. How do I set secrets there?" -> profile "Tech lead on
  the payments API." and fact "The payments API is deployed on Fly.io." The question is dropped.
- "Moved from Lyon to Annecy last month." (dated 2026-05-02) -> profile "Lives in Annecy since around April 2026."
- "Please never use print for logging, we use structlog." -> preference (key `logging`) "Wants structlog for logging,
  never print."
- "What's a good restaurant around here?" -> nothing. "I'm thinking about learning Rust." -> nothing.

Quote verbatim from a user message. Never use `assertion: inferred`. Utility (1-5): 5 = a constraint the assistant
must respect in almost every answer (an allergy, a required convention, a format they asked for, where they live);
4 = another lasting circumstance (job, project, stack, family, dated commitment); 3 = mildly useful (a hobby, a
taste); 1-2 = trivia.
