"""Area summaries: derived text, never evidence.

After a run, every area that gained or changed a linked row gets one call to the judge model. The prompt holds the
area title and the `content` of its active linked rows, nothing else. The result is a new version of the area row
(`created_by = "summarizer"`), labelled auto-summary."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from memhub.pipeline.llm import invoke_structured
from memhub.store import MemoryStore, as_vector_list

AUTO_SUMMARY = "auto-summary"


class Summary(BaseModel):
    summary: str


@dataclass
class SummaryResult:
    written: bool = False
    tokens_in: int = 0
    tokens_out: int = 0


def rows_fingerprint(rows: list[dict]) -> str:
    """Identifies a set of linked rows: a new or edited row is a new id, so any change to the set changes it."""
    return hashlib.sha1("|".join(sorted(str(r["id"]) for r in rows)).encode()).hexdigest()


def _prompt(title: str, rows: list[dict]) -> str:
    contents = "\n".join(f"- {r['content']}" for r in rows)
    return (
        "Summarize what is known about one topic from the statements below, in two sentences at most, in the "
        "language of the statements. Each statement is about one person (the user): keep that person as the "
        "subject and never make an institution, service or place the subject of something the person did or has. Write in the third person, never \"you\" or \"the user\". "
        "Add nothing that is not in the statements.\n"
        f"Topic: {title}\nStatements:\n{contents}"
    )


def summarize_area(judge: Any, *, cur, store: MemoryStore, area_memory_id: uuid.UUID, retries: int = 0) -> SummaryResult:
    """No call when the area has no active linked rows, or the row set is unchanged since the last summary."""
    area = store.get_active(cur, area_memory_id)
    if area is None or area["type"] != "area":
        return SummaryResult()
    rows = store.linked_rows(cur, area_memory_id)
    fingerprint = rows_fingerprint(rows)
    if not rows or area["payload"].get("summary_rows") == fingerprint:
        return SummaryResult()
    title = area["payload"]["title"]
    parsed, tokens_in, tokens_out = invoke_structured(
        judge, Summary, [("human", _prompt(title, rows))], retries=retries, what="area summary",
    )
    result = SummaryResult(tokens_in=tokens_in, tokens_out=tokens_out)
    text = parsed.summary.strip() if parsed is not None else ""
    if not text:
        return result
    payload = {
        **area["payload"], "summary": text, "content": f"{title}: {text}", "summary_label": AUTO_SUMMARY,
        "summary_rows": fingerprint,
    }
    store.edit_memory(
        cur, area_memory_id, content=payload["content"], payload=payload, entities=[],
        embedding=as_vector_list(area["embedding"]), verified=False, created_by="summarizer",
    )
    result.written = True
    return result
