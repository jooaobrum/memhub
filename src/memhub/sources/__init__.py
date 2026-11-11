"""Trace sources the ingest pipeline reads from (see `base.py` for the adapter contract)."""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


def build_source(cfg: Any, *, workspace_id: str, base_dir: Path) -> Any:
    """The source a `SourceConfig` describes: a built-in kind, or a project's own `package.module:Class`."""
    if cfg.kind == "jsonl":
        from memhub.sources.jsonl import JSONLSource

        return JSONLSource(cfg, workspace_id=workspace_id, base_dir=base_dir)
    if cfg.kind == "mlflow":
        from memhub.sources.mlflow import MLflowSource

        return MLflowSource(cfg, workspace_id=workspace_id)
    if cfg.kind == "claude_code":
        from memhub.sources.claude_code import ClaudeCodeSource

        return ClaudeCodeSource(cfg)
    if cfg.kind == "sql":
        from memhub.sources.sql import SQLSource

        return SQLSource(cfg, workspace_id=workspace_id)
    if ":" in cfg.kind:  # "package.module:Class": the project's own adapter (see sources/base.py)
        module, _, name = cfg.kind.partition(":")
        return getattr(importlib.import_module(module), name)(cfg, workspace_id=workspace_id)
    raise ValueError(f"unsupported source kind {cfg.kind!r}")
