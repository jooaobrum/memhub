---
types:
  incident:
    fields: {equipment: str, fault_code: str, fix: str}
    content_template: "{equipment} fault {fault_code}: {fix}"
    scopes: [workspace]      # shared by the team, after a supervisor approves it
    on_remember: true        # a `/remember` order may record one
---
# What to remember

Maintenance assistant. Keep durable knowledge about machines: model, location, known quirks, and fixes that worked
(an `incident`). Never keep questions, greetings or measurements that only describe the moment.
