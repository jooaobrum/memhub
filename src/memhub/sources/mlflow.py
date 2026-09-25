"""MLflow traces -> Interactions. Requires the optional `memhub[mlflow]` extra."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterator

from memhub.config import SourceConfig
from memhub.sources.base import Interaction

DEFAULT_FIELDS = {"thread_id": "mlflow.trace.session", "user_id": "mlflow.trace.user"}


def dig(value: Any, path: str | None) -> Any:
    """Follow a dotted path through dicts and lists ("messages.-1.content"); None if absent."""
    for key in path.split(".") if path else []:
        try:
            value = value[int(key)] if isinstance(value, list) else value[key]
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return value


def parse(raw: str | None) -> Any:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return raw


class MLflowSource:
    """`fields`: thread_id/user_id name a trace tag or metadata key; user_content/assistant_content
    are dotted paths into the request/response (unset: the payload itself, when it is a string);
    `metadata` lists tag names copied onto each Interaction."""

    def __init__(self, source_cfg: SourceConfig, *, workspace_id: str):
        self.cfg = source_cfg
        self.workspace_id = workspace_id
        self.skipped = 0

    def read(self) -> Iterator[Interaction]:
        from mlflow import MlflowClient

        client = MlflowClient(tracking_uri=self.cfg.tracking_uri)
        experiment = client.get_experiment_by_name(self.cfg.experiment)
        if experiment is None:
            raise ValueError(f"MLflow experiment not found: {self.cfg.experiment}")
        fields = {**DEFAULT_FIELDS, **self.cfg.fields}
        token = None
        while True:
            page = client.search_traces(
                locations=[experiment.experiment_id],
                order_by=["timestamp_ms ASC"],
                page_token=token,
            )
            for trace in page:
                yield from self._turn(trace, fields)
            token = page.token
            if not token:
                return

    def _turn(self, trace: Any, fields: dict[str, Any]) -> list[Interaction]:
        info = trace.info
        labels = {**info.trace_metadata, **info.tags}
        thread_id = labels.get(fields["thread_id"])
        user = dig(parse(trace.data.request), fields.get("user_content"))
        assistant = dig(parse(trace.data.response), fields.get("assistant_content"))
        if not (thread_id and isinstance(user, str) and user and isinstance(assistant, str) and assistant):
            self.skipped += 1
            return []
        metadata = {k: labels[k] for k in fields.get("metadata", []) if k in labels}
        if info.state.value == "ERROR":
            metadata["error"] = True
        when = datetime.fromtimestamp(info.request_time / 1000, tz=timezone.utc)
        common = dict(
            thread_id=thread_id,
            user_id=labels.get(fields["user_id"]) or "unknown",
            workspace_id=self.workspace_id,
            timestamp=when,
            trace_id=info.trace_id,
        )
        return [
            Interaction(message_id=info.trace_id, role="user", content=user, metadata=dict(metadata), **common),
            Interaction(message_id=f"{info.trace_id}:a", role="assistant", content=assistant, metadata=dict(metadata), **common),
        ]
