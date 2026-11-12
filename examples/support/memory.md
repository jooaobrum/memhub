---
types:
  account:                      # declared in yaml, no Python
    fields:
      company: {type: "str", description: "the customer's company"}
      plan: {type: "str?", description: "subscription plan, if said"}
      contact: {type: "str?", description: "admin or main contact person"}
    content_template: "{company} - plan {plan}, contact {contact}"
    scopes: [workspace]
    retrieval: search
  preference: {class: "memhub.types:Preference", retrieval: always, keyed: true, strict_keys: false, keys: {language: "language of the answers", detail: "length of the answers"}, scopes: [user]}
  ticket:
    fields:
      symptom: {type: str, description: "what the customer observed"}
      root_cause: {type: str}
      resolution: {type: str, description: "what fixed it"}
      outcome: {type: str}
    content_template: "Symptom: {symptom} Root cause: {root_cause} Resolution: {resolution} Outcome: {outcome}"
    extract: false
    on_remember: true
    scopes: [workspace]
  fact: {class: "memhub.types:Fact", retrieval: search, scopes: [workspace]}
---
Customer-support agent. Keep durable facts the customer states about their company, account, setup and integrations (account, fact) and how they want answers (preference). Never keep questions, one-off requests or what the agent says.
