#!/usr/bin/env python3
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "benchmark_summary.csv"

rows = list(csv.DictReader(path.open(encoding="utf-8")))
print("HISTORICAL TWITTER BENCHMARK")
print("Frozen benchmark size: 1,142 pairs")
print()

for row in rows:
    accuracy = float(row["accuracy_pct"])
    reported = float(row["S85"])
    recomputed = (accuracy / 100.0 - 0.50) / (0.85 - 0.50)
    print(
        f"{row['predictor']}: accuracy={accuracy:.1f}% "
        f"S85(reported)={reported:.3f} S85(from rounded accuracy)={recomputed:.3f}"
    )
    assert abs(recomputed - reported) <= 0.0015

print()
print("PASS: all S85 values agree with the paper definition to rounding tolerance.")
print("Wilson confidence intervals in the CSV are frozen reported values; this script")
print("does not invent exact integer counts from rounded percentages.")
