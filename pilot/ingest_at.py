"""Ingest with a chosen clock, for data whose timestamps are ahead of the real time (the fake batch).
Usage: .venv/bin/python pilot/ingest_at.py <config.yaml> <ISO now> [--reprocess]"""
import json, sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from memhub.cli import _build_source, _service
from memhub.config import build_chat_model
from memhub.pipeline.ingest import ingest_source

cfg, now = Path(sys.argv[1]), datetime.fromisoformat(sys.argv[2])
service = _service(cfg)
s = service.settings
summary = ingest_source(
    store=service.store, settings=s, registry=service.registry, source=_build_source(s, "jsonl", cfg), source_name="jsonl",
    extractor=build_chat_model(s.llm.extractor), judge=build_chat_model(s.llm.judge), embeddings=service.embeddings,
    reprocess="--reprocess" in sys.argv, now=now,
)
print(json.dumps(asdict(summary), indent=2, default=str))
