"""Score the human-labelled sample: python pilot/score_labels.py [pilot/run4_label_sample.csv]

Columns to fill by hand: correct, useful, grounded (y/n). Precision = share of labelled rows that are
correct AND useful; hallucination rate = share of labelled rows that are not grounded.
Targets: precision >= 0.8, hallucination rate 0.
"""
import csv
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "pilot/run4_label_sample.csv"
rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
yn = lambda v: (v or "").strip().lower()
labelled = [r for r in rows if all(yn(r[c]) in ("y", "n") for c in ("correct", "useful", "grounded"))]
if not labelled:
    sys.exit("no fully labelled rows: fill correct/useful/grounded with y or n")
if len(labelled) < len(rows):
    print(f"warning: only {len(labelled)} of {len(rows)} rows are fully labelled; unlabelled rows are ignored")
n = len(labelled)
precision = sum(yn(r["correct"]) == "y" and yn(r["useful"]) == "y" for r in labelled) / n
halluc = sum(yn(r["grounded"]) == "n" for r in labelled) / n
print(f"labelled: {n}")
print(f"precision (correct AND useful): {precision:.3f}  (target >= 0.80)")
print(f"hallucination rate (not grounded): {halluc:.3f}  (target 0)")
if n < 50:
    print("note: fewer than 50 labelled rows")
