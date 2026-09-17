# Reproducibility guide

## Frozen-result principle

The paper evaluates hosted LLMs whose implementations and routing can change after the experiment. The repository therefore distinguishes:

1. **frozen-result reproduction** — recompute statistics from the archived predictions/data; and
2. **live inference reruns** — optional network/API executions that may not exactly match the historical model behavior.

The first is the authoritative reproduction target.

## Beat the Robot

```bash
python repro/01_beat_the_robot/analysis/reproduce_beat_results.py
```

Checks 70 completed Round-1 sessions, 1,400 judgments, the 69/70 game scoreline, fixed-model matched comparisons, disagreement analyses, close-call stratification, random-choice calibration, and sensitivity to removing the sole 16/20 session.

## Fresh Bluesky replication

```bash
python repro/02_bluesky_replication/analysis/reproduce_bluesky_results.py
```

Checks the frozen 48-pair confirmatory set and current-vs-older complete-run panel statistics. The component README also documents the production Jetstream discovery command and an offline finalizer replay from the included frozen candidate table and AppView cache.

## Historical Twitter benchmark

```bash
python repro/03_historical_twitter_benchmark/analysis/reproduce_benchmark_summary.py
```

Checks the published aggregate benchmark table and `S85` normalization. The final v6 evaluator entry point and execution documentation are included. Complete frozen pair-level predictions for all six models are pinned to the exact public `human-retweet-v4` study snapshot; `analysis/fetch_and_verify_pair_level_snapshot.py` downloads that file, verifies its SHA-256 hash, and recomputes the six model accuracies. Raw provider-response/attempt traces are not needed for frozen-result reproduction and are not duplicated here.

## Working text-only ceiling

```bash
python repro/04_ceiling_model/reproduce_ceiling.py
```

Reproduces the 85.3% central illustration and the approximately 82–90% sensitivity envelope.
