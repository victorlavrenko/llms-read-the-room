# LLMs Read the Room Better Than Humans

Reproducibility repository for **“LLMs Read the Room Better Than Humans”** by Victor Lavrenko (PeaceTech VC).

The paper tests whether contemporary large language models can predict which of two human-written messages actually received more social transmission. It combines three empirical components: a 1,142-pair historical Twitter benchmark, a 70-session contemporary human-vs-model web study, and a fresh August 2026 Bluesky replication.

## Headline results

- Historical Twitter benchmark: best LLM **79.7%**, versus **61.3%** individual-human and **73.0%** 39-person-majority reference points.
- Beat the Robot: **1,400** Round-1 human judgments from **70** completed sessions; players lost **69 of 70** game comparisons.
- Fresh Bluesky replication: Gemini **36/48 = 75.0%**; five complete current-model runs average **65.8%** versus **55.2%** for four complete older-model runs (**+10.6 pp**, exact paired sign-flip **p=.030**).

## Repository map

- `paper/` — arXiv-oriented two-column LaTeX source, figures, and compiled PDF.
- `repro/01_beat_the_robot/` — deidentified human-study data, study protocol, matched model comparisons, disagreement/uncertainty analyses, and study-critical source snapshot.
- `repro/02_bluesky_replication/` — Bluesky discovery/finalization code, frozen candidate/final datasets, exact evaluator, frozen model outputs, and paired statistical analysis.
- `repro/03_historical_twitter_benchmark/` — frozen benchmark results, a hash-pinned pair-level prediction snapshot fetcher, and final v6 evaluator entry point/documentation.
- `repro/04_ceiling_model/` — working text-only ceiling and `S85` calculation.
- `scripts/verify_all.py` — runs the frozen-data reproduction checks.
- `release/` — release manifest and checksums.

## Reproduce the frozen analyses

Requires Python 3.10+; the four verification analyses use only the standard library.

```bash
python scripts/verify_all.py
```

Or run components independently:

```bash
python repro/01_beat_the_robot/analysis/reproduce_beat_results.py
python repro/02_bluesky_replication/analysis/reproduce_bluesky_results.py
python repro/03_historical_twitter_benchmark/analysis/reproduce_benchmark_summary.py
python repro/04_ceiling_model/reproduce_ceiling.py
```

These reproduce the reported frozen statistics without making live model API calls. Hosted model endpoints can change over time, so frozen outputs—not future reruns—are the scientific record of the evaluated systems.

## Build the paper

The arXiv source intentionally uses the standard `article` class rather than an ACM proceedings template.

```bash
cd paper
pdflatex main.tex
pdflatex main.tex
```

## Human-study implementation

The deployed Beat the Robot application is available at:

- live game: https://beat-the-robot.peacetech.vc/
- source: https://github.com/victorlavrenko/beat-the-robot
- study snapshot: `972ad75d923faf7e96ded1d40f6841e3fc33f6fe`
- recorded experiment version: `human-retweet-v4`

The reproduction data in this repository are deidentified. Original nicknames, session/participant UUIDs, and exact public-game timestamps are not released.

## Data and reuse

Some files contain public social-media text, post identifiers, URLs, or dataset-derived material whose rights may belong to their original authors or platforms. See `DATA_NOTICE.md` before redistribution. No API keys, access tokens, or private production exports are included.

No blanket software/data license has been selected in this repository package. Add the licenses you want before encouraging third-party reuse; publishing the repository without a license still permits viewing and verification but does not grant general reuse rights.

## Citation

See `CITATION.cff`. The arXiv identifier should be added after the paper is announced on arXiv.
