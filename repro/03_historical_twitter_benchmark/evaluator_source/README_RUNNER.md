# Historical Twitter evaluator

The included `run_eval.py` is the entry point of the final adaptive/resumable v6 evaluator
used for the 1,142-pair historical Twitter benchmark.

The v6 evaluator was the successor to a v5 full-test run. Its migration procedure preserved
the existing workspace's prompt, seed, pair ordering, six logical model identifiers, and
already-successful calls rather than silently changing the experiment. The production
runner supported concurrent model routes, durable SQLite/WAL state, retries, and per-cell
execution provenance.

The explicit production invocation was:

```bash
python run_eval.py   --workspace runs/full-test-v6   --global-concurrency 80   --openrouter-concurrency 64   --hf-concurrency 32   --max-tokens 8192   --retry-max-tokens 16384   --max-attempts 5   --heartbeat 15   --report-every 100
```

Expected v6 workspace outputs included `results/summary.csv`,
`results/predictions.csv`, `results/attempts.csv`, a prompt snapshot, route configuration,
and the authoritative SQLite state.

## Scope of this release

The final aggregate benchmark results are preserved in `benchmark_summary.csv`, together
with the v6 evaluator entry point and the production invocation. This component documents the benchmark procedure and supports verification of the reported aggregate results. It is not packaged as a standalone live-API rerun environment. The fresh Bluesky component separately includes frozen data, evaluator outputs, and an offline finalization pipeline.

No API credentials or provider secrets are included.
