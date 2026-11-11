"""N4: Postgres search latency before and after the storage ports (AD-014: reported, not a CI gate).

    python scripts/bench_search.py --dsn postgresql://postgres:postgres@localhost:5432/memhub [--base 2a58e6c]

Checks out `--base` into a temporary git worktree, then runs the same measurement against it and against this
working tree, alternating `--rounds` times so neither side always meets the fuller database: 200 rows like the
tests' data, `MemoryService.search` 200 times per round. Prints the median of each side's round medians, in
milliseconds, and their ratio (N4 asks for <= 1.10).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MEASURE = r"""
import json, statistics, sys, time, uuid
sys.path.insert(0, sys.argv[3])
from memhub.config import EmbeddingConfig, Settings, TypeConfig
from memhub.service import MemoryService
from memhub.store import Actor, MemoryStore
from memhub.types import TypeRegistry
from tests.fakes import FakeEmbeddings

dsn, runs = sys.argv[1], int(sys.argv[2])
emb = FakeEmbeddings(dims=16)
store = MemoryStore(dsn, f"bench{uuid.uuid4().hex[:8]}")
store.init(embedding_model="fake-embed", dims=16)
settings = Settings(project_prefix="bench", workspace_default="ws", scopes=["user", "workspace"],
                    embeddings=EmbeddingConfig(model="fake-embed", dims=16),
                    types={"fact": TypeConfig(**{"class": "memhub.types:Fact"})})
service = MemoryService(store=store, settings=settings, registry=TypeRegistry(), embeddings=emb)
admin = Actor(id="admin", roles=["workspace_admin"])
words = "tea coffee lisbon paris press night shift answers portuguese machine oven belt".split()
for i in range(200):
    text = " ".join(words[(i + j) % len(words)] for j in range(4)) + f" item {i}"
    service.add(admin, type="fact", scope="user", fields={"content": text}, user_id="alice")
for _ in range(10):
    service.search(admin, "tea in lisbon", user_id="alice", k=5)
times = []
for _ in range(runs):
    t0 = time.perf_counter()
    service.search(admin, "tea in lisbon", user_id="alice", k=5)
    times.append((time.perf_counter() - t0) * 1000)
print(json.dumps({"median_ms": statistics.median(times)}))
"""


def measure(src: Path, dsn: str, runs: int) -> float:
    env = {**os.environ, "PYTHONPATH": f"{src / 'src'}{os.pathsep}{src}"}
    out = subprocess.run([sys.executable, "-c", MEASURE, dsn, str(runs), str(src / "src")], env=env,
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"measuring {src} failed:\n{out.stderr[-2000:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])["median_ms"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--base", default="2a58e6c")
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--rounds", type=int, default=5, help="base and tree alternate this many times")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        base_dir = Path(tmp) / "base"
        subprocess.run(["git", "worktree", "add", "--detach", str(base_dir), args.base], cwd=ROOT, check=True,
                       capture_output=True)
        try:
            pairs = [(measure(base_dir, args.dsn, args.runs), measure(ROOT, args.dsn, args.runs)) for _ in range(args.rounds)]
            before = statistics.median(b for b, _ in pairs)
            after = statistics.median(a for _, a in pairs)
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(base_dir)], cwd=ROOT, capture_output=True)
    print(f"base {args.base}: median {before:.3f} ms")
    print(f"this tree:      median {after:.3f} ms")
    print(f"ratio:          {after / before:.3f} (N4 asks for <= 1.10)")


if __name__ == "__main__":
    main()
