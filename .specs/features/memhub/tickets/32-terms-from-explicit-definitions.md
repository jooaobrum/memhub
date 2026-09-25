# 32 — Terms: a shared glossary from explicit definitions

**What to build:** The workspace keeps a small glossary of terms and their synonyms, for example CNH = carteira
de motorista = permis de conduire, or CAF. A term is proposed only when a message explicitly defines it,
and an admin approves it before it is used. Nothing is created from terms that merely appear together.

- New built-in type `Term`: `term`, `expansion` (optional), `aliases` (interchangeable) and `related` (same
  topic). Its `content` is written so it embeds, e.g. `CNH (carteira nacional de habilitação): carteira de motorista, permis de conduire`.
- Workspace scope, so a term is a `candidate` in the existing review queue until approved. Habitantes adds
  `workspace` to its `scopes` for this kind only (every other kind stays user scope).
- Terms are a secondary kind: small cap, built after the core kinds.
- The extractor may return a `term` candidate only in a project that has the type enabled. The prompt says: only
  when a message explicitly defines the term (for example "CNH significa carteira nacional de habilitação",
  "CNH é a mesma coisa que carteira de motorista"), and every alias must appear in the quote.
- Grounding: an alias that is not in the evidence quotes drops the candidate as `ungrounded`.
- Reconcile: a candidate whose `term` matches an active term (case-insensitive) merges its aliases into it as a
  new version, and is capped at `terms.max_aliases` (5).
- Cap: `terms.max_per_workspace` (300). Over the cap the candidate is dropped as `term_cap`.
- `memhub list --type term` and the queue show terms; approve and reject work as for any workspace memory.
- Not in this ticket: query expansion (33) and finding terms by co-occurrence (out of scope).

**Blocked by:** 26 — Remove the automatic context Episode

**Status:** done

- [ ] AC 26: an explicit definition creates a workspace `candidate` term with the defining quote as evidence, and it is not returned by search until an admin approves it
- [ ] A term whose alias is not in the quote is dropped as `ungrounded`; no term is created from a segment that merely mentions two terms
- [ ] A second definition of the same term adds its aliases to the existing term as a new version, up to five aliases
- [ ] The term cap drops the candidate as `term_cap`
- [ ] The Habitantes config loads with `term` enabled and `scopes: [user, workspace]`, and only `term` is workspace scope
