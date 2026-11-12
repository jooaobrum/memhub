"""C35-C36: the real-model rerun of the e2e scenarios on project-defined memory is recorded, with the files it used."""
from __future__ import annotations

import re
from pathlib import Path

from memhub.memory_file import split
from memhub.settings import resolve

ROOT = Path(__file__).resolve().parents[1]
E2E = ROOT / ".specs" / "e2e"
FILES = {"S1": "starter.md", "S2": "s2-support.md", "S3": "starter.md", "S4": "s4-birds.md", "S5": "starter.md",
         "S6": "s6-maintenance.md"}


def _rerun() -> str:
    text = (E2E / "scenarios.md").read_text()
    assert "## Rerun: project-defined memory" in text
    return text.split("## Rerun: project-defined memory", 1)[1].split("\n## ", 1)[0]


def test_rerun_recorded():
    """C35"""
    section = _rerun()
    assert re.match(r" \(\d{4}-\d{2}-\d{2}\)", section)  # dated
    rows = {m.group(1): m.group(0) for m in re.finditer(r"^\| (S[1-6]) \|.*$", section, re.M)}
    assert sorted(rows) == ["S1", "S2", "S3", "S4", "S5", "S6"]
    for scenario, row in rows.items():
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert f"memory/{FILES[scenario]}" in cells[1], scenario
        assert cells[2] == "pass", scenario
        assert cells[3], scenario  # bugs found and their fix, or "none"
    for name in set(FILES.values()):
        path = E2E / "memory" / name
        resolve({"project": "p", "memory": str(path)}, environ={})


def test_s4_birds_recorded():
    """C36"""
    text = (E2E / "memory" / "s4-birds.md").read_text()
    front, _ = split(text, "s4-birds.md")
    front_lines = text.split("---\n")[1].strip().splitlines()
    assert len(front_lines) <= 20 and "null" not in text and "threshold" not in text and front["strict"] is True
    s4 = _rerun().split("### S4", 1)[1].split("\n### ", 1)[0]
    written = s4.split("Written:", 1)[1].split("Not stored:", 1)[0]
    assert "Hoopoe" in written and "Heron" in written and "species_note" in written
    not_stored = s4.split("Not stored:", 1)[1]
    for thing in ("job", "move", "phone", "coffee", "binocular", "falcon"):
        assert thing in not_stored, thing
