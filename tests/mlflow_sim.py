"""Write real MLflow traces into a local tracking store (no server, no network)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any


def write_traces(tracking_uri: str, experiment: str, turns: list[dict[str, Any]]) -> list[str]:
    """One trace per turn: {request, response, session?, user?, tags?, metadata?, error?}.

    `request`/`response` are the traced input/output (str or JSON-able). Returns the trace ids in order.
    """
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    if mlflow.get_experiment_by_name(experiment) is None:
        artifacts = Path(tracking_uri.removeprefix("sqlite:///")).parent / "artifacts"
        mlflow.create_experiment(experiment, artifact_location=artifacts.as_uri())
    mlflow.set_experiment(experiment)
    ids = []
    for turn in turns:
        metadata = dict(turn.get("metadata", {}))
        if turn.get("session"):
            metadata["mlflow.trace.session"] = turn["session"]
        if turn.get("user"):
            metadata["mlflow.trace.user"] = turn["user"]
        try:
            with mlflow.start_span("chat") as span:
                span.set_inputs(turn["request"])
                mlflow.update_current_trace(tags=turn.get("tags", {}), metadata=metadata)
                if turn.get("error"):
                    raise RuntimeError("boom")
                span.set_outputs(turn["response"])
        except RuntimeError:
            pass
        ids.append(mlflow.get_last_active_trace_id())
        time.sleep(0.01)  # distinct request_time (ms) so ordering is deterministic
    mlflow.flush_trace_async_logging()
    return ids
