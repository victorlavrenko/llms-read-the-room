#!/usr/bin/env python3
from __future__ import annotations
import csv, math, random
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SEED=20260907; BOOTSTRAPS=100_000

def load(path):
    rows=[]
    with path.open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            t=str(r['correct']).strip().lower(); r['_correct']=1 if t=='true' else 0 if t=='false' else None; rows.append(r)
    return rows

def wilson(k,n,z=1.959963984540054):
    p=k/n; den=1+z*z/n; center=(p+z*z/(2*n))/den; half=z*math.sqrt((p*(1-p)/n)+z*z/(4*n*n))/den; return center-half,center+half

def stats(rows):
    d=defaultdict(list)
    for r in rows:d[r['model']].append(r['_correct'])
    return {m:{'attempted':len(xs),'valid':sum(x is not None for x in xs),'correct':sum(x for x in xs if x is not None)} for m,xs in d.items()}
cur=load(ROOT/'replication/runs/current/scored_predictions.csv'); old=load(ROOT/'replication/runs/older/scored_predictions.csv')
cs,os_=stats(cur),stats(old)
cm=sorted(m for m,s in cs.items() if s['valid']==48); om=sorted(m for m,s in os_.items() if s['valid']==48)
curmean=sum(cs[m]['correct']/48 for m in cm)/len(cm); oldmean=sum(os_[m]['correct']/48 for m in om)/len(om); gap=curmean-oldmean
g='google/gemini-3.7-flash'; gk,gn=cs[g]['correct'],cs[g]['valid']; glo,ghi=wilson(gk,gn)
cb,ob=defaultdict(dict),defaultdict(dict)
for r in cur: cb[r['pair_id']][r['model']]=r['_correct']
for r in old: ob[r['pair_id']][r['model']]=r['_correct']
pids=sorted(cb); diffs=[]
for pid in pids:
    a=[cb[pid][m] for m in cm]; b=[ob[pid][m] for m in om]; assert all(x is not None for x in a+b); diffs.append(Fraction(sum(a),len(a))-Fraction(sum(b),len(b)))
scale=1
for d in diffs: scale=math.lcm(scale,d.denominator)
weights=[abs(int(d*scale)) for d in diffs if d]; observed=abs(int(sum(diffs,Fraction(0))*scale)); dist=Counter({0:1})
for w in weights:
    nxt=Counter()
    for s,n in dist.items(): nxt[s+w]+=n; nxt[s-w]+=n
    dist=nxt
p2=sum(n for s,n in dist.items() if abs(s)>=observed)/(2**len(weights))
rng=random.Random(SEED); means=[]; positive=0
for _ in range(BOOTSTRAPS):
    s=sum(diffs[rng.randrange(len(diffs))] for _ in range(len(diffs))); m=float(s/len(diffs)); means.append(m); positive+=(m>0)
means.sort(); lo=means[int(.025*BOOTSTRAPS)]; hi=means[int(.975*BOOTSTRAPS)-1]
print('FRESH BLUESKY REPLICATION')
print(f'pairs={len(pids)}')
print(f'Gemini={gk}/{gn}={100*gk/gn:.1f}% (Wilson 95% CI {100*glo:.1f}-{100*ghi:.1f}%)')
print(f'current complete models={len(cm)}'); [print(f'  {m}: {cs[m]["correct"]}/48={100*cs[m]["correct"]/48:.2f}%') for m in cm]
for m,s in sorted(cs.items()):
    if s['valid']!=48: print(f'  excluded incomplete current run: {m}: valid={s["valid"]}/48')
print(f'older complete models={len(om)}'); [print(f'  {m}: {os_[m]["correct"]}/48={100*os_[m]["correct"]/48:.2f}%') for m in om]
print(f'current panel mean={100*curmean:.1f}%')
print(f'older panel mean={100*oldmean:.1f}%')
print(f'difference={100*gap:.1f} percentage points')
print(f'exact two-sided sign-flip p={p2:.6f}')
print(f'paired bootstrap seed={SEED}, B={BOOTSTRAPS}')
print(f'bootstrap positive differences={100*positive/BOOTSTRAPS:.1f}%')
print(f'bootstrap percentile 95% CI={100*lo:.1f} to {100*hi:.1f} percentage points')
assert len(pids)==48 and gk==36 and gn==48 and len(cm)==5 and len(om)==4
assert round(100*curmean,1)==65.8 and round(100*oldmean,1)==55.2 and round(100*gap,1)==10.6 and round(p2,3)==.030 and round(100*positive/BOOTSTRAPS,1)==98.8
