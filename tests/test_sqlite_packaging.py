"""What PR 4 ships beside the code: sqlite-vec as an extra, and the SQLite docs."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sqlite_vec_is_an_extra_not_a_core_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    extras = project["optional-dependencies"]
    assert not any(d.replace("_", "-").startswith("sqlite-vec") for d in project["dependencies"])
    assert any(d.startswith("sqlite-vec") for d in extras["sqlite"])
    assert any(d.startswith("sqlite-vec") for d in extras["dev"])  # the with-vec contract leg never skips (AD-017)


def test_sqlite_docs():
    text = (ROOT / "docs" / "sqlite.md").read_text()
    for needle in ("sqlite:///", "sqlite3.Connection", "memhub[sqlite]", "50k rows per owner", "MEMHUB_SQLITE_VEC",
                   "parallel_ingest is not available on sqlite", "database_schema"):
        assert needle in text, needle
    assert re.search(r"[Ww]ithout `memhub\[sqlite\]`[^.]*Python", text)  # the fallback is said, not implied
    assert "docs/sqlite.md" in (ROOT / "README.md").read_text()


def test_wheel_contains_the_sqlite_schema(tmp_path, monkeypatch):
    import zipfile

    monkeypatch.chdir(ROOT)  # hatchling reads pyproject.toml from the cwd
    from hatchling.build import build_wheel

    names = zipfile.ZipFile(tmp_path / build_wheel(str(tmp_path))).namelist()
    assert "memhub/storage/sqlite_schema.sql" in names
