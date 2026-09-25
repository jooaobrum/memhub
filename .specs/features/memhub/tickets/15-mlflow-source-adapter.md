# 15 — MLflow traces source adapter

**What to build:** A deployment can configure a source with `kind: mlflow` (`tracking_uri`, `experiment`) and run `memhub ingest --source <name>` to pull MLflow traces. The adapter normalises them into `Interaction`s and sends them through the same pipeline as JSONL. The pilot does not use it.

**Blocked by:** 06 — Tracer bullet: JSONL ingest → extract → route → ledger

**Status:** done

- [ ] MLflow traces map to `Interaction`s with thread, user, message and trace ids, role, content and timestamp
- [ ] Traces missing a thread or message id are skipped and counted
- [ ] Watermarks work the same as for JSONL (a second run with no new traces makes no LLM calls)
- [ ] Tests use recorded or fake MLflow trace fixtures (no live tracking server), and the MLflow dependency is an optional extra
