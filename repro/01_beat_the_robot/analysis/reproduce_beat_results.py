#!/usr/bin/env python3
from __future__ import annotations
import csv, math, statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRIALS = list(csv.DictReader((ROOT / "data/round1_trials.csv").open(encoding="utf-8")))
SESS = list(csv.DictReader((ROOT / "data/round1_sessions.csv").open(encoding="utf-8")))

MODELS = [
    ("gemini", "Gemini 3.7 Flash"),
    ("gpt56", "GPT-5.6 Sol"),
    ("qwen_max", "Qwen 3.7 Max"),
    ("claude", "Claude Opus 4.8"),
    ("qwen_flash", "Qwen 3.7 Flash"),
    ("deepseek", "DeepSeek V4 Flash"),
]

def pct(a, b): return 100.0 * a / b
def pbeat(r, n=20):
    return sum(math.comb(n, k) for k in range(r + 1, n + 1)) / (2 ** n)

print("PRIMARY ROUND")
human_correct = sum(int(r["player_correct"]) for r in TRIALS)
print(f"sessions={len(SESS)} judgments={len(TRIALS)} human={human_correct}/{len(TRIALS)}={pct(human_correct,len(TRIALS)):.2f}%")
outcomes = {x: sum(r["outcome_vs_comparison_robot"] == x for r in SESS)
            for x in ("human_win","tie","robot_win")}
print(f"human-vs-featured/legacy comparison W/T/L={outcomes['human_win']}/{outcomes['tie']}/{outcomes['robot_win']}")

print("\nFIXED MODEL MATCHED COMPARISONS")
by_session = defaultdict(list)
for r in TRIALS:
    by_session[r["session"]].append(r)

for key, name in MODELS:
    answered = [r for r in TRIALS if r[f"{key}_correct"] != ""]
    model_correct = sum(int(r[f"{key}_correct"]) for r in answered)
    complete = []
    for sid, rows in by_session.items():
        rr = [r for r in rows if r[f"{key}_correct"] != ""]
        if len(rr) == 20:
            hs = sum(int(r["player_correct"]) for r in rr)
            ms = sum(int(r[f"{key}_correct"]) for r in rr)
            complete.append((hs,ms))
    hcorr = sum(h for h,m in complete)
    mcorr = sum(m for h,m in complete)
    n = 20 * len(complete)
    w = sum(h>m for h,m in complete); t=sum(h==m for h,m in complete); l=sum(h<m for h,m in complete)
    print(f"{name}: human={pct(hcorr,n):.1f}% model={pct(mcorr,n):.1f}% complete_sessions={len(complete)} W/T/L={w}/{t}/{l}")

print("\nDISAGREEMENTS")
for key, name in MODELS:
    rr = [r for r in TRIALS if r[f"{key}_prediction"] != "" and r["player_prediction"] != r[f"{key}_prediction"]]
    c = sum(int(r[f"{key}_correct"]) for r in rr)
    print(f"{name}: n={len(rr)} model_correct={pct(c,len(rr)):.2f}%")

print("\nCLOSE-CALL METADATA")
recorded = [r for r in TRIALS if r["player_close_call"] != ""]
print(f"human close-call field recorded={len(recorded)} missing/not-collected={len(TRIALS)-len(recorded)}")
for key, name in MODELS:
    nc=[r for r in TRIALS if r[f"{key}_close_call"]=="0" and r[f"{key}_correct"]!=""]
    cc=[r for r in TRIALS if r[f"{key}_close_call"]=="1" and r[f"{key}_correct"]!=""]
    print(f"{name}: non-close={sum(int(r[f'{key}_correct']) for r in nc)}/{len(nc)}={pct(sum(int(r[f'{key}_correct']) for r in nc),len(nc)):.2f}%"
          f" close={sum(int(r[f'{key}_correct']) for r in cc)}/{len(cc)}={pct(sum(int(r[f'{key}_correct']) for r in cc),len(cc)):.2f}%")

print("\nRANDOM-CHOICE CALIBRATION")
primary=[int(r["primary_robot_score"]) for r in SESS]
featured=[int(r["comparison_robot_score"]) for r in SESS]
for label,scores in [("session_primary_robot",primary),("featured_best_of_panel_or_legacy",featured)]:
    ps=[pbeat(s) for s in scores]
    print(f"{label}: expected_wins={sum(ps):.3f}; P(at least one)={(1-math.prod(1-p for p in ps))*100:.1f}%")

all6_ps=[]
beat_all6=0
for sid, rows in by_session.items():
    scores=[]
    full=True
    for key,_ in MODELS:
        vals=[r[f"{key}_correct"] for r in rows]
        if any(v=="" for v in vals):
            full=False; break
        scores.append(sum(int(v) for v in vals))
    if full:
        hs=sum(int(r["player_correct"]) for r in rows)
        mx=max(scores)
        beat_all6 += hs>mx
        all6_ps.append(pbeat(mx))
print(f"all-six complete sessions={len(all6_ps)} observed beat-all-six={beat_all6}; random P(at least one)={(1-math.prod(1-p for p in all6_ps))*100:.1f}%")

print("\nSENSITIVITY")
top=max(SESS, key=lambda r:int(r["human_score"]))
without=[r for r in TRIALS if r["session"]!=top["session"]]
print(f"top human score={top['human_score']}/20; human accuracy excluding top={pct(sum(int(r['player_correct']) for r in without),len(without)):.1f}%")

# Frozen checks
assert len(SESS)==70
assert len(TRIALS)==1400
assert human_correct==760
assert outcomes=={"human_win":1,"tie":0,"robot_win":69}
assert len(recorded)==1180
assert len(TRIALS)-len(recorded)==220
