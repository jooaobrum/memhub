---
types:
  account:                                  # a company's plan and contact, shared by every agent after review
    fields:
      company: {type: str, description: "the customer's company"}
      plan: {type: "str?", description: "subscription plan, if said"}
      contact: {type: "str?", description: "admin or main contact person"}
    content_template: "{company}[ - plan {plan}][, contact {contact}]"
    scopes: [workspace]
  preference: {class: "memhub.types:Preference", retrieval: always, keyed: true, strict_keys: false, scopes: [user],
               keys: {language: "language of the answers", detail: "length of the answers"}}
  fact: {class: "memhub.types:Fact", scopes: [workspace]}     # a company fact, shared after review
  ticket: {fields: {symptom: str, root_cause: str, resolution: str, outcome: str}, extract: false, on_remember: true,
           scopes: [workspace], content_template: "Symptom: {symptom} Root cause: {root_cause} Resolution: {resolution}"}
---
# What to remember

Customer-support agent. Keep durable facts the customer states about their company, account, setup and integrations
(`account`, `fact`), and how they want answers (`preference`).

- Never keep questions, one-off requests or incidents that are already solved, or what the agent says.
- Write `content` as your own third-person sentence ("Wants every answer in Spanish"), never the customer's words
  copied as they are; the verbatim words go in the evidence quote.
