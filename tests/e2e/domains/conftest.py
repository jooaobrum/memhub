"""`E2E_DUMP_DIR=<dir> pytest tests/e2e/domains`: each scenario also writes `<dir>/<scenario>.json` with the
project's memory.md, what happened (pushes, agent turns, CLI commands), every ledger row, and the pages each user and
the workspace would read. For looking at how the memories were organized; off by default."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.e2e.domains import harness


def _pages(p: harness.Project, rows: list[dict]) -> dict[str, str]:
    users = sorted({r["user_id"] for r in rows if r["user_id"]})
    out = {}
    for who, personal, label in [*((u, True, f"users/{u}") for u in users),
                                 *(((users or ["reader"])[0], False, f"team/{p.workspace}"),)]:
        try:
            mem = p.memory.scope(**p.ids(who, workspace=None))
            out[f"{label}/README.md"] = mem.render(personal=personal)
            for page in mem.pages(personal=personal):
                out[f"{label}/{page['key']}.md"] = mem.render(page["key"], personal=personal)
        except Exception as exc:  # noqa: BLE001 - a dump never fails a scenario
            out[f"{label}/error"] = repr(exc)
    return out


@pytest.fixture(autouse=True)
def _dump(request):
    harness.LAST.clear()
    yield
    target = os.environ.get("E2E_DUMP_DIR")
    if not target or not harness.LAST:
        return
    p = harness.LAST[0]
    with p.memory.store.unit_of_work() as uow:
        rows = [{k: v for k, v in r.items() if k != "embedding"} for r in uow.memories.list_memories()]
    rows.sort(key=lambda r: (str(r["created_at"]), r["version"]))
    data = {"scenario": request.node.name, "file": Path(str(request.node.fspath)).name,
            "doc": (request.node.function.__doc__ or "").strip(), "backend": p.backend_kind,
            "workspace": p.workspace, "memory_md": p.memory_md, "log": p.log, "rows": rows,
            "pages": _pages(p, rows)}
    Path(target).mkdir(parents=True, exist_ok=True)
    (Path(target) / f"{request.node.name}.json").write_text(json.dumps(data, indent=1, default=str))
