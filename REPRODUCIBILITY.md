# Reproducibility guide

## Frozen-result principle

The paper evaluates hosted LLMs whose implementations and routing can change after the experiment. The repository therefore distinguishes:

1. **frozen-result reproduction** — recompute statistics from archived predictions/data; and
2. **live inference reruns** — optional network/API executions that may not exactly match historical model behavior.

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

Checks the frozen 48-pair confirmatory set and current-vs-older complete-run panel statistics. The component README documents the production Jetstream discovery command and offline finalizer replay.

## Historical Twitter benchmark

```bash
python repro/03_historical_twitter_benchmark/analysis/reproduce_benchmark_summary.py
```

Checks the published aggregate benchmark table and `S85` normalization. Complete frozen pair-level predictions for all six models are pinned to the exact public `human-retweet-v4` study snapshot; the fetch/verify script validates its SHA-256 hash and recomputes the six model accuracies.

## Practical text-only ceiling

```bash
python repro/04_ceiling_model/reproduce_ceiling.py
```

Reproduces the 85.3% central illustration and approximately 82–90% sensitivity envelope.

## Prompt/order/confidence robustness

```bash
python repro/05_robustness_checks/analysis/reproduce_robustness_results.py
```

Reconstructs the deterministic 100-pair sample, deduplicates the archived OpenRouter JSONL by job ID, and recomputes the paper's prompt robustness, X/Y-reversal consistency, confidence calibration, and selective-accuracy results for Gemini 3.7 Flash, GPT-5.6 Sol, and Claude Opus 4.8. No live API calls are made.

## Run everything

```bash
python scripts/verify_all.py
```
