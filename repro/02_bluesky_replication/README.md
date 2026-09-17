# Fresh Bluesky replication

This is the public fresh-data component supporting the paper's contamination-robustness
check. It preserves the collection/finalization code, the frozen 48-pair dataset, the exact
LLM evaluator, frozen current- and older-model outputs, and a deterministic analysis script.

## 1. Discovery crawl provenance

The production discovery run used:

```bash
go run . \
  --hours 84 \
  --end-days-ago 8 \
  --max-gap-hours 12 \
  --top 500 \
  --buckets 32 \
  --resolve-profiles=false \
  --progress-every 100000 \
  --out discovery-3d
```

The resolved frozen window was:

- start: 2026-08-27T01:53:45.1699838Z
- end:   2026-08-30T13:53:45.1699838Z

The crawl produced 35,946 eligible candidate rows from 3,175 authors with at least one
eligible repeated-link pair. The hosted replay hit a byte quota, so this is an incomplete
convenience sample rather than a census of Bluesky.

The raw Jetstream bucket files are intentionally omitted from this compact release because
they are large and unnecessary for reproducing the retained-pair finalization. The package
instead includes the frozen `candidate_pairs.csv`, crawl state/summary, and the complete
AppView hydration cache required for offline finalization.

## 2. Reproduce the deterministic 70-pair decisive set offline

From `collection/`:

```bash
python source/bluesky_finalize_pairs.py frozen_discovery --offline --out reproduced-final
```

The frozen finalizer applies the 12-hour same-author/same-structured-destination,
materially-different-text, mature-outcome, and decisive 8-repost / 5-margin / 2x rules.
Its deterministic X/Y seed is 20260907.

The finalizer yields 70 decisive pairs. The paper's 48-pair confirmatory dataset is a
manually audited frozen subset of those 70. Both the 70-pair audit and the final 48-pair
audit are included so this reduction is visible rather than hidden.

## 3. Frozen 48-pair model evaluation

The model-facing data are `replication/data/questions.csv`; the gold outcomes are held
separately in `answer_key.csv`. The exact runner is
`replication/runner/run_bluesky_eval.py`.

The original current-model command was:

```bash
python run_bluesky_eval.py \
  --concurrency 12 \
  --batch-size 48 \
  --max-tokens 4096 \
  --outdir runs/bluesky-48
```

Frozen raw/scored outputs are provided, so API credentials are not needed to verify the
reported results.

## 4. Reproduce the paper statistics

From the package root:

```bash
python analysis/reproduce_bluesky_results.py
```

The revised reproducible panel comparison uses complete 48/48 model runs only:
five current models and four older models. The archived DeepSeek fresh run returned only
28 valid outputs and is retained but excluded from this panel-level comparison.

The script reproduces:

- Gemini: 36/48 = 75.0%, Wilson 95% CI 61.2-85.1%;
- five-complete-current-model mean: 65.8%;
- four-complete-older-model mean: 55.2%;
- difference: +10.6 percentage points;
- exact two-sided paired sign-flip test over the 48 case-level panel differences: p=.030;
- paired 100,000-resample bootstrap (seed 20260907): 95% percentile interval
  +1.4 to +19.6 percentage points; 98.8% of bootstrap differences are positive.

These values replace earlier draft values (55.5%, +10.4 pp, p≈.022, 99.2%) that were
derived from an intermediate analysis snapshot. The frozen final run artifacts in this
package are treated as authoritative.

## Anonymity

The package contains no author names, author email addresses, author project URLs, or
author repository URLs. Public Bluesky handles/post identifiers and destination URLs are
retained because they are necessary data provenance.
