# LLMs Read the Room Better Than Humans

Reproducibility repository for **“LLMs Read the Room Better Than Humans”** by Victor Lavrenko (PeaceTech VC).

The paper asks whether LLMs may already know which human narratives audiences value even when their own generated prose is generic. It probes that latent audience model through **comparative social-transmission prediction**: given two human-written messages from the same author about the same destination, which one did people choose to retransmit? Sharing is treated as a consequential revealed-preference signal because it requires carrying a message into one's own social context rather than merely rating it.

## Headline results

- Historical Twitter benchmark: best LLM **79.7%**, versus **61.3%** average individual-human and **73.0%** 39-judgment-majority reference points; Tan et al.'s held-out supervised classifier reached **65.6%**.
- Beat the Robot: **1,400** Round-1 judgments from **70 completed sessions**; on identical items Gemini scores **77.7%** versus **54.1%** for recorded human choices (**+23.6 pp**, crossed session/item bootstrap **+17.6 to +29.4 pp**).
- Robustness: on a deterministic 100-pair diagnostic, equivalent prompts and reversed X/Y order preserve frontier performance; the most-confident quartile reaches **92–96%** accuracy across Gemini, GPT-5.6 Sol, and Claude Opus 4.8.
- Fresh Bluesky replication: Gemini **36/48 = 75.0%**; five complete current-model runs average **65.8%** versus **55.2%** for four complete older-model runs (**+10.6 pp**, exact paired sign-flip **p=.030**).
- Practical text-only ceiling: central sensitivity estimate **85.3%**, with an approximate **82–90%** envelope.

## Repository map

- `paper/` — arXiv-oriented two-column LaTeX source, figures, and compiled PDF.
- `repro/01_beat_the_robot/` — deidentified human-study data, study protocol, matched model comparisons, disagreement/uncertainty analyses, and study-critical source snapshot.
- `repro/02_bluesky_replication/` — Bluesky discovery/finalization code, frozen candidate/final datasets, exact evaluator, frozen model outputs, and paired statistical analysis.
- `repro/03_historical_twitter_benchmark/` — frozen benchmark results, hash-pinned pair-level snapshot verification, and evaluator entry point/documentation.
- `repro/04_ceiling_model/` — practical text-only ceiling and `S85` calculation.
- `repro/05_robustness_checks/` — exact 100-pair prompt/order/confidence robustness protocol, archived raw model calls, CSV analyses, and frozen-result verifier.
- `scripts/verify_all.py` — runs all frozen-data reproduction checks.
- `release/` — release notes and checksums.

## Reproduce the frozen analyses

Requires Python 3.10+. The frozen-result checks use only the standard library.

```bash
python scripts/verify_all.py
```

Or run components independently:

```bash
python repro/01_beat_the_robot/analysis/reproduce_beat_results.py
python repro/02_bluesky_replication/analysis/reproduce_bluesky_results.py
python repro/03_historical_twitter_benchmark/analysis/reproduce_benchmark_summary.py
python repro/04_ceiling_model/reproduce_ceiling.py
python repro/05_robustness_checks/analysis/reproduce_robustness_results.py
```

These reproduce the reported frozen statistics without making live model API calls. Hosted model endpoints can change over time, so frozen outputs—not future reruns—are the scientific record of the evaluated systems.

## Build the paper

```bash
cd paper
pdflatex main.tex
pdflatex main.tex
```

## Human-study implementation

- live game: https://beat-the-robot.peacetech.vc/
- source: https://github.com/victorlavrenko/beat-the-robot
- study snapshot: `972ad75d923faf7e96ded1d40f6841e3fc33f6fe`
- recorded experiment version: `human-retweet-v4`

The released human-study data are deidentified. Original nicknames, session/participant UUIDs, and exact public-game timestamps are not released.

## Data and reuse

Some files contain public social-media text, post identifiers, URLs, or dataset-derived material whose rights may belong to their original authors or platforms. See `DATA_NOTICE.md` before redistribution. No API keys, access tokens, or private production exports are included.

No blanket software/data license has been selected in this repository package. Add the licenses you want before encouraging third-party reuse; publishing the repository without a license still permits viewing and verification but does not grant general reuse rights.

## Citation

See `CITATION.cff`. The arXiv identifier should be added after the paper is announced on arXiv.
