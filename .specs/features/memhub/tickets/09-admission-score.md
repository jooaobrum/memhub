# 09 — Admission score

**What to build:** Only candidates that are worth keeping become memories. Each grounded candidate gets an auditable admission score, `score = Σ wᵢ·fᵢ`, with the weights and threshold taken from config:

| Feature | Computation |
|---|---|
| `utility` | (utility − 1) / 4 |
| `evidence` | 1.0 if user/tool evidence is present; 0.4 if only assistant evidence (Episodes) |
| `novelty` | 1 − highest cosine similarity to active memories of the same type and scope |
| `type_prior` | from config, per type |
| `signals` | 0.5, then + 0.5 for a correction or thumbs-up and − 0.5 for a thumbs-down, rephrase or error, clamped to [0, 1] |

Candidates with `score < threshold` are dropped as `low_score` and logged in `dropped`. The score is stored on the memory row. A correction gets a segment past the prefilter, but its candidates must still pass the score.

**Blocked by:** 07 — Grounding checks with recorded drops; 08 — Implicit signals and the prefilter

**Status:** done

- [ ] Each feature is unit-tested at its boundaries, including clamping
- [ ] Changing the weights or threshold in config changes admission without any code change
- [ ] A candidate below the threshold is dropped with reason `low_score`
- [ ] Created memories store their `score`
- [ ] Novelty is computed with fake embeddings in tests
