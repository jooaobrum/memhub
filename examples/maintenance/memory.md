---
types:
  incident:
    class: "maint_types:Incident"            # equipment, fault_code, fix
    content_template: "{equipment} fault {fault_code}: {fix}"
    scopes: [workspace]
    on_remember: true
---
Maintenance assistant. Keep durable knowledge about machines: model, location, known quirks, and fixes that worked (an `incident`). Never keep questions, greetings or measurements that only describe the moment.
