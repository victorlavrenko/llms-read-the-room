#!/usr/bin/env python3
from __future__ import annotations
import csv, hashlib, json, math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "llm_calls.jsonl"
AN = ROOT / "analysis"
MODELS = [
    "google/gemini-3.7-flash",
    "openai/gpt-5.6-sol",
    "anthropic/claude-opus-4.8",
]
PROMPTS = ["paper_exact", "concise"]

def close(a, b, tol=1e-9):
    if a is None or b is None: return a == b
    return abs(float(a)-float(b)) <= tol

def latest_rows():
    latest = {}
    for line in RAW.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        r = json.loads(line)
        latest[r["job_id"]] = r
    return list(latest.values())

def sample_ids():
    ids = [str(i) for i in range(1, 1143)]
    ids.sort(key=lambda pid: hashlib.sha256(f"20260917|{pid}".encode()).hexdigest())
    selected = ids[:100]
    selected.sort(key=int)
    return selected

def wilson(k,n,z=1.959963984540054):
    if not n: return (None,None)
    p=k/n; d=1+z*z/n
    c=(p+z*z/(2*n))/d
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return c-h,c+h

def read_csv(name):
    with (AN/name).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def canon(pred, orientation, x_source):
    other = "second" if x_source == "first" else "first"
    if orientation == "frozen": return x_source if pred == "X" else other
    if orientation == "swapped": return other if pred == "X" else x_source
    raise ValueError(orientation)

def main():
    rows=latest_rows(); ids=sample_ids(); idset=set(ids)
    target=[r for r in rows if r.get("model") in MODELS and r.get("pair_id") in idset]

    # Prompt robustness
    expected={(r["model"],r["prompt_variant"]):r for r in read_csv("prompt_robustness.csv")}
    for model in MODELS:
        for pv in PROMPTS:
            rs=[r for r in target if r.get("experiment")=="prompt_robustness" and r.get("model")==model and r.get("prompt_variant")==pv and r.get("prediction") in {"X","Y"}]
            assert len(rs)==100, (model,pv,len(rs))
            correct=sum(r["prediction"]==r["gold"] for r in rs)
            acc=correct/100
            exp=expected[(model,pv)]
            assert int(exp["valid"])==100 and int(exp["correct"])==correct and close(exp["accuracy"],acc)
    # Prompt agreement/range
    summary={r["model"]:r for r in read_csv("prompt_robustness_model_summary.csv")}
    for model in MODELS:
        by={pv:{} for pv in PROMPTS}
        for r in target:
            if r.get("experiment")=="prompt_robustness" and r.get("model")==model and r.get("prompt_variant") in PROMPTS and r.get("prediction") in {"X","Y"}:
                by[r["prompt_variant"]][r["pair_id"]]=r["prediction"]
        agree=sum(by[PROMPTS[0]][pid]==by[PROMPTS[1]][pid] for pid in ids)/100
        accs=[]
        for pv in PROMPTS:
            rr=[r for r in target if r.get("experiment")=="prompt_robustness" and r.get("model")==model and r.get("prompt_variant")==pv and r.get("prediction") in {"X","Y"}]
            accs.append(sum(r["prediction"]==r["gold"] for r in rr)/100)
        exp=summary[model]
        assert close(exp["unanimous_prediction_rate"],agree)
        assert close(exp["accuracy_range_pp"],100*(max(accs)-min(accs)))

    # Orientation swap
    expected_o={r["model"]:r for r in read_csv("orientation_swap.csv")}
    for model in MODELS:
        f={r["pair_id"]:r for r in target if r.get("experiment")=="prompt_robustness" and r.get("model")==model and r.get("prompt_variant")=="paper_exact" and r.get("prediction") in {"X","Y"}}
        sw={r["pair_id"]:r for r in target if r.get("experiment")=="orientation_swap" and r.get("model")==model and r.get("prediction") in {"X","Y"}}
        assert set(f)==idset and set(sw)==idset
        same=fc=sc=0
        for pid in ids:
            xsrc=f[pid]["metadata"]["x_source"]
            same += canon(f[pid]["prediction"],"frozen",xsrc)==canon(sw[pid]["prediction"],"swapped",xsrc)
            fc += f[pid]["prediction"]==f[pid]["gold"]
            sc += sw[pid]["prediction"]==sw[pid]["gold"]
        exp=expected_o[model]
        assert int(exp["paired_valid"])==100
        assert close(exp["canonical_prediction_consistency"],same/100)
        assert close(exp["frozen_accuracy"],fc/100)
        assert close(exp["swapped_accuracy"],sc/100)

    # Confidence calibration / selective accuracy
    expected_c={r["model"]:r for r in read_csv("confidence_calibration.csv")}
    for model in MODELS:
        rm={r["pair_id"]:r for r in target if r.get("experiment")=="confidence" and r.get("model")==model and r.get("prediction") in {"X","Y"} and isinstance(r.get("confidence"),int)}
        rs=[rm[pid] for pid in ids]
        assert len(rs)==100
        ys=[1 if r["prediction"]==r["gold"] else 0 for r in rs]
        ps=[r["confidence"]/100 for r in rs]
        acc=sum(ys)/100; mean=sum(ps)/100
        brier=sum((p-y)**2 for p,y in zip(ps,ys))/100
        ece=0.0
        for lo in [0.5,0.6,0.7,0.8,0.9]:
            hi=lo+0.1+1e-12
            inds=[i for i,p in enumerate(ps) if (lo <= p < hi) or (lo>=0.9 and lo<=p<=1.0)]
            if inds:
                ece += len(inds)/100*abs(sum(ps[i] for i in inds)/len(inds)-sum(ys[i] for i in inds)/len(inds))
        order=sorted(range(100),key=lambda i:ps[i],reverse=True)
        def top(frac):
            n=max(1,math.ceil(100*frac)); return sum(ys[i] for i in order[:n])/n
        exp=expected_c[model]
        vals={"accuracy":acc,"mean_reported_confidence":mean,"brier_score":brier,"ece_5bins":ece,
              "top25pct_confidence_accuracy":top(.25),"top50pct_confidence_accuracy":top(.50),"top75pct_confidence_accuracy":top(.75)}
        for k,v in vals.items(): assert close(exp[k],v,1e-12),(model,k,exp[k],v)

    print("ROBUSTNESS CHECKS PASSED")
    for model in MODELS:
        p=summary[model]; o=expected_o[model]; c=expected_c[model]
        print(f"{model}: prompt range {float(p['accuracy_range_pp']):.1f} pp; prompt agreement {100*float(p['unanimous_prediction_rate']):.1f}%; order consistency {100*float(o['canonical_prediction_consistency']):.1f}%; top-25% confidence accuracy {100*float(c['top25pct_confidence_accuracy']):.1f}%")

if __name__ == "__main__": main()
