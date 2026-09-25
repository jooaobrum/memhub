"""A structured-output call that samples again when the model returns unparseable JSON."""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

log = logging.getLogger(__name__)


def invoke_structured(model: Any, schema: Any, messages: list, *, retries: int = 0, what: str = "call") -> tuple[Any, int, int]:
    """(parsed or None, tokens in, tokens out). Cheap models truncate their JSON now and then (`EOF while parsing`);
    that is a ValidationError raised by the provider parser, not a `parsing_error`, and must not abort the run."""
    tokens_in = tokens_out = 0
    for attempt in range(1 + retries):
        try:
            out = model.with_structured_output(schema, include_raw=True).invoke(messages)
        except (ValidationError, OutputParserException):
            log.warning("unparseable %s (attempt %d)", what, attempt + 1)
            continue
        usage = getattr(out["raw"], "usage_metadata", None) or {}
        tokens_in += usage.get("input_tokens", 0)
        tokens_out += usage.get("output_tokens", 0)
        if out["parsing_error"] is None and out["parsed"] is not None:
            return out["parsed"], tokens_in, tokens_out
        log.warning("unparseable %s (attempt %d)", what, attempt + 1)
    return None, tokens_in, tokens_out
