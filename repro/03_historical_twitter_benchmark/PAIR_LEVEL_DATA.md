# Pair-level frozen benchmark data

The complete 1,142-pair frozen study object is already public in the Beat the Robot study repository at the exact `human-retweet-v4` snapshot:

- repository: https://github.com/victorlavrenko/beat-the-robot
- commit: `972ad75d923faf7e96ded1d40f6841e3fc33f6fe`
- file: `data/retweet_experiment_v1.private.json`
- expected SHA-256: `d656faca50d64790acbb0b908063eff6a8e7af81d9b3586a85ea275295b3825f`

Despite the historical filename `private.json`, the file is part of the public Git repository. It contains the 1,142 pair texts, frozen gold/X-Y orientation, six model predictions, and model close-call flags used by the web-study instrument. It does **not** contain human participant records.

To fetch it, verify the hash, and recompute all six pair-level model accuracies:

```bash
python analysis/fetch_and_verify_pair_level_snapshot.py
```

The separate evaluator documentation records the final adaptive v6 inference entry point and execution procedure. Raw provider responses/attempt traces are not required to recompute the published model accuracies once the frozen predictions above are used.
