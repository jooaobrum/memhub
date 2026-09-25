# 37 — Missing memories: preferences, facts and conflicts on the five-user fake batch

**What was wrong:** On the new fake traces (5 users, 144 turns) the ledger held only a few Profile rows: no
Preference, no Fact, no update or conflict. `pilot/run7_*.json` are the runs that found and closed each cause.

| # | Cause | Fix |
|---|-------|-----|
| 1 | One thread = one segment = at most `max_candidates` (3) memories, so a 36-turn thread kept its first three facts and never saw the rest | `ingestion.segment_max_user_turns` cuts a thread into slices of N user turns; `max_candidates` 5 per slice |
| 2 | Admission score: `novelty = 1 - similarity` taxed every memory about the same person (statements about one person embed at 0.3–0.6), and `threshold: 0.8` needed utility 5 once the ledger had a row | novelty is 1 below the conflict band and falls to 0 at the duplicate threshold; threshold 0.75 |
| 3 | Updates and contradictions were only detected inside the similarity band 0.80–0.92, but "é vegano" / "voltou a comer carne" embed at 0.64, "Fontaine" / "Meylan" at 0.56, "Crous" / "Île Verte" at 0.51 | the judge now sees the nearest rows (`reconcile.compare_floor`, `compare_max`) in ONE call and picks the one the candidate is about; `updates` supersedes, `conflicts` opens a candidate |
| 4 | The same change landed in `fact` for one message and `profile` for another, so it was never compared | `reconcile.compare_across: [[profile, fact]]`; across types the outcome is always a conflict for review (a version of another type cannot replace) |
| 5 | The cheap extractor and the judge return truncated JSON in ~1 of 5 calls (`EOF while parsing`): a lost slice was recorded as `extract_error` and never retried, and a judge failure aborted the whole thread | `pipeline/llm.invoke_structured` samples again (`extraction.retries`); a slice that still fails is extracted in two halves; long assistant answers are shown cut (`ingestion.assistant_chars`) |
| 6 | The final pass told the model to "summarize the whole conversation in an episode": every episode was a list of questions or a restated fact | prompt rewritten; `question_episode` guard; `ingestion.verify_episodes` (judge confirms the user told situation, action and outcome) |
| 7 | Preference keys `style` / `detail` collided (bullets and "no emoji" fought for one row; "com fontes" ended in `detail`) | keys `language, scope, detail, format, emoji, tone, sources, address`; the prompt says one memory per key, self-contained content, and that a one-off request is not a preference |
| 8 | Instruction bugs: rule (e) told the model to skip facts "implied by the topic" ("sou vegetariana, tem restaurante?"); "profile is ONLY the listed keys" though Profile has none; dated commitments ("meu pai vem em outubro") were treated as intentions | rewritten; dated commitments are facts with `valid_until` |
| 9 | Claims lost for cosmetic reasons: a hallucinated `entities` list dropped the whole candidate; "próximo ano" dropped the claim | entities are stripped when none are configured; `ingestion.repair_relative_time` has the judge date the claim from the message date |
| 10 | Wrong claims got in: memories copied out of the prompt's examples, facts about a mother or child written as about the user, needs ("Precisa de…") and "tem interesse em…" stored as facts, a `key` borrowed from another type on a Profile, a quote cited on the neighbouring message | guards `need_or_request`, `too_thin`, `the user` prefix stripped, key cleared; quote re-pointed to the message that says it; `ingestion.verify_claims` puts only suspicious claims (nothing of the memory in its quote, or the quote is about someone else and the memory names nobody) to the judge |

| 11 | `/remember` existed only as the agent tool `propose_memory`; in a trace it was ordinary text, so whether it was kept depended on the extractor's strictness | `ingestion.remember_command` (default `/remember`). The order either says what to keep or points at the conversation ("save this case"): then the `remember_context` (30) messages above it are read from the WHOLE thread (not only the 5-turn slice), by the judge model, and a solved problem becomes a `case` (`symptom`, `root_cause`, `action`, `outcome`, enabled with `on_remember: true`). The schema and the rules are configuration: any class with `on_remember: true`, `content_template` on the type composes its `content`, and `extraction.remember_instructions` replaces the built-in instructions. Stored active with `created_by: remember`; no need/thin guards, no judge second-guessing, no admission threshold. The background extractor no longer reads the command messages |
| 12 | In a long, fact-free conversation the extractor turned questions into memories ("Tem desconto no cinema", a "scope" preference "Procura informações…") | `questioned` quotes are checked by the judge (`question_only`); searches are refused as preferences |

**Known limits (not fixed):** the extractor still forgets some facts from run to run (recall is model-bound; about
90% of the injected facts appear in any one run); a change written as prose ("É vegano, mas voltou a comer carne")
is an update, not a two-row conflict; a one-off "dessa vez, mais detalhe" is sometimes stored as a preference and
lands as a conflict candidate for review.

- [x] `pytest` green (unit + Docker integration)
- [x] Five users ingested; second ingest makes no LLM calls
- [x] HTML regenerated: [pilot/run7_memories.html](../pilot/run7_memories.html)
