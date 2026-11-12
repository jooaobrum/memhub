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


def test_wheel_has_no_presets(tmp_path, monkeypatch):
    """C20: no type set ships in the package."""
    import zipfile

    monkeypatch.chdir(ROOT)  # hatchling reads pyproject.toml from the cwd
    from hatchling.build import build_wheel

    wheel = tmp_path / build_wheel(str(tmp_path))
    names = zipfile.ZipFile(wheel).namelist()
    assert any(n.startswith("memhub/") for n in names)  # the wheel is the package
    assert not [n for n in names if n.startswith("memhub/presets")]


def test_readme_quickstart_commands():
    from typer.testing import CliRunner

    from memhub import cli

    readme = (ROOT / "README.md").read_text()
    quick = readme.split("## Quickstart", 1)[1].split("\n## ", 1)[0]
    for needle in ("memhub init", "memory.md", "memhub add", "memhub search", "Memory()"):
        assert needle in quick, needle
    commands = set()
    for block in re.findall(r"```bash\n(.*?)```", quick, re.S):
        for line in block.replace("\\\n", " ").splitlines():
            for part in line.split("#")[0].split("&&"):
                words = part.split()
                if words[:1] == ["memhub"]:
                    name = [words[1]] + ([words[2]] if words[1] in ("config", "areas") and not words[2].startswith("-") else [])
                    commands.add(tuple(name))
    assert {c[0] for c in commands} >= {"init", "add", "search", "doctor"}
    for command in commands:
        result = CliRunner().invoke(cli.app, [*command, "--help"])
        assert result.exit_code == 0, (command, result.output)
