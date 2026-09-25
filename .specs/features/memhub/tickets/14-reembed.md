# 14 — `memhub reembed` after an embedding-model change

**What to build:** An operator can switch embedding provider or model by editing the config and running `memhub reembed`. The command recomputes the embedding of every active version in batches, changes the vector column if the dims changed, rebuilds the HNSW index, and saves the new model and dims. After that, the other commands, which refused to run while the config did not match (ticket 01), work again.

**Blocked by:** 03 — Semantic search with citable results

**Status:** done

- [ ] After a model change, commands other than `reembed` refuse to run until `reembed` completes
- [ ] `reembed` recomputes the embeddings of all active versions in batches, and search works afterwards
- [ ] A dims change is handled (column retyped, index rebuilt)
- [ ] An interrupted `reembed` can be re-run safely
