"""C29-C33: the onboarding docs teach memory.md, load, and name only commands the CLI has."""
from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from memhub import cli
from memhub.memory_file import split
from memhub.settings import resolve

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
GUIDES = sorted((DOCS / "guides").glob("*.md"))


def _fences(text: str) -> list[str]:
    return re.findall(r"^```[\w-]*\n(.*?)^```", text, re.S | re.M)


def test_memory_docs_exist():
    """C29"""
    ref = (DOCS / "memory.md").read_text()
    for heading in ("## The file", "## Types: the short forms", "## Types: the long form", "## strict",
                    "## Starter types", "## A worked example"):
        assert heading in ref, heading
    setup = (DOCS / "memory-setup.md").read_text()
    for step in ("What should be remembered?", "What must never be remembered?", "How does it group?",
                 "memory.md", "add(messages, infer=True)", "out.dropped"):
        assert step in setup, step


def test_memory_blocks_load(tmp_path):
    """C30: every fenced block that opens with `---` is a memory.md that loads."""
    files = [ROOT / "README.md", *DOCS.rglob("*.md"), *(ROOT / "examples").rglob("*.md")]
    seen = 0
    for doc in files:
        for i, block in enumerate(_fences(doc.read_text())):
            if not block.startswith("---\n"):
                continue
            path = tmp_path / f"{doc.stem}-{i}.md"
            path.write_text(block)
            split(block, path.name)  # the frontmatter is closed and valid yaml
            resolve({"project": "p", "memory": str(path)}, environ={})
            seen += 1
    assert seen >= 6  # README, memory.md (2), quickstart, guides 04-06


def test_no_preset_left():
    """C31: no preset in the docs, the examples or the package, except the refusal of an old `preset:` key."""
    refusal = ROOT / "src" / "memhub" / "settings.py"
    allowed = ("PRESET_REMOVED", '"preset" in layer', "MEMHUB_PRESET")
    files = [ROOT / "README.md", ROOT / "memhub.example.yaml"]
    for folder in ("src", "docs", "examples"):
        files += [p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts
                  and p.suffix in (".py", ".md", ".yaml", ".yml", ".html", ".json", ".txt", ".toml")]
    found = []
    for p in files:
        for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if "preset" in line.lower() and not (p == refusal and any(a in line for a in allowed)):
                found.append(f"{p.relative_to(ROOT)}:{n}")
    assert found == []


def test_memory_docs_name_no_vendor():
    """C32"""
    for name in ("memory.md", "memory-setup.md"):
        assert "Claude" not in (DOCS / name).read_text(), name


def _commands(text: str) -> list[list[str]]:
    out = []
    for block in _fences(text):
        for line in block.replace("\\\n", " ").splitlines():
            for part in re.split(r"&&|\|", line.split(" #")[0]):
                words = part.split()
                if words[:1] == ["memhub"] and len(words) > 1:
                    out.append(words[1:])
    return out


def test_doc_commands_exist():
    """C33: every `memhub <command> --flag` in the README and the guides is an option of that command."""
    runner = CliRunner()
    checked = 0
    for doc in [ROOT / "README.md", *GUIDES]:
        for words in _commands(doc.read_text()):
            name = [words[0]] + ([words[1]] if words[0] in ("config", "areas") and len(words) > 1 and not words[1].startswith("-") else [])
            result = runner.invoke(cli.app, [*name, "--help"], env={"COLUMNS": "250", "TERMINAL_WIDTH": "250"})
            assert result.exit_code == 0, (doc.name, name, result.output)
            for flag in (w.split("=")[0] for w in words if w.startswith("--")):
                assert flag in result.output, (doc.name, name, flag)
                checked += 1
    assert checked > 10


def test_quickstart_order_and_birds_guide():
    """C33: the README quickstart goes init -> memory.md -> add -> search; guide 06 holds the short birds file."""
    quick = (ROOT / "README.md").read_text().split("## Quickstart", 1)[1].split("\n## ", 1)[0]
    at = [quick.index(s) for s in ("memhub init", "memory.md", "memhub add", "memhub search")]
    assert at == sorted(at)
    guide = (DOCS / "guides" / "06-your-own-domain.md").read_text()
    birds = [b for b in _fences(guide) if b.startswith("---\n") and "sighting" in b]
    assert len(birds) == 1
    front = birds[0].split("---\n")[1]
    assert len(front.strip().splitlines()) <= 20 and "null" not in front
    assert "admission.threshold" not in birds[0] and "--preset" not in guide
