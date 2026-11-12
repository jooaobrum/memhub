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
Never store secrets, one-off questions, or something only the assistant said.
