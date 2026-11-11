"""What ships: the public names, the dependency pins, and the Azure recipe with no Azure code in the core."""
from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_exports():
    import memhub
    from memhub.service import ValidationError

    for name in ("Memory", "AddResult", "MemoryEvent", "Dropped", "CallContext", "ValidationError"):
        assert hasattr(memhub, name) and name in memhub.__all__, name
    assert memhub.ValidationError is ValidationError  # one class, whichever import path the app uses


def test_pyproject_dependencies():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    assert "langchain>=1.0" in deps
    assert any(re.fullmatch(r"psycopg-pool(>=[\d.]+)?", d) for d in deps)


def test_azure_recipe():
    text = (ROOT / "docs" / "azure.md").read_text()
    for needle in ("ConnectionPool", "connection_class", "get_token", "https://ossrdbms-aad.database.windows.net/.default",
                   "AzureChatOpenAI", "AzureOpenAIEmbeddings", "azure_ad_token_provider", "Memory("):
        assert needle in text, needle
    lifetimes = [int(v) for v in re.findall(r"max_lifetime\s*=\s*(\d+)", text)]
    assert lifetimes and all(v < 3600 for v in lifetimes)


def test_core_imports_no_azure():
    for path in (ROOT / "src" / "memhub").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not any(n == "azure" or n.startswith("azure.") or n.startswith("langchain_openai") for n in names), path


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
