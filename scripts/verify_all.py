#!/usr/bin/env python3
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
checks = [
    ROOT / "repro/01_beat_the_robot/analysis/reproduce_beat_results.py",
    ROOT / "repro/02_bluesky_replication/analysis/reproduce_bluesky_results.py",
    ROOT / "repro/03_historical_twitter_benchmark/analysis/reproduce_benchmark_summary.py",
    ROOT / "repro/04_ceiling_model/reproduce_ceiling.py",
]
for script in checks:
    print(f"\n=== {script.relative_to(ROOT)} ===")
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
print("\nALL FROZEN-RESULT CHECKS PASSED")
