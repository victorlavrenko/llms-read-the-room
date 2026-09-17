# Beat the Robot reproduction data

This directory contains the deidentified public-game component used in the paper. It is intentionally self-contained and contains no original participant names, original session/participant identifiers, exact timestamps, or deployment identifiers.

## What is included

- 1,400 deidentified Round-1 judgments from 70 completed primary sessions.
- Fixed six-model matched comparisons and head-to-head session outcomes.
- Player–model disagreement analysis.
- Model and human close-call analysis with missingness represented correctly.
- Round-level and coarse-profile aggregates.
- Random-choice calibration and participant-authenticity sensitivity checks.
- The study protocol and human-subjects context.
- Study-critical assignment/prompt/scoring source code from the v4 deployment.
- A standard-library-only reproduction script.

Run:

```bash
python analysis/reproduce_beat_results.py
```

Expected frozen headline output includes:
- human Round-1 accuracy: **760/1,400 = 54.29%**
- game comparison: **1 human win, 0 ties, 69 robot wins**
- Gemini matched accuracy: **77.68%** on 1,380 judgments
- player close-call metadata: **1,180 recorded, 220 not collected**

## Important data correction made in this package

An earlier deidentified working CSV had encoded the 220 pre-close-call judgments as `0`.
That is not faithful to the frozen production records. In this package they are **blank**,
because the close-call field did not yet exist. This matches the manuscript's stated
analysis rule that those 220 observations are not counted as non-close.

## Important score-definition audit finding

The frozen data contain two different robot-score notions:

1. `primary_robot_score`: the robot score stored with each session completion.
2. `featured best-of-panel`: the highest-scoring fully answering model on the session's
   item set (available for 69 of the 70 sessions; the one oldest session predates the panel).

The observed human W/T/L is 1/0/69 under either comparison, so the headline result is
unchanged. However, the random-choice calibration differs:

- primary/session robot scores: expected random wins = **1.114**,
  P(at least one) = **69.2%**
- best-of-panel featured scores (plus the one legacy fallback): expected random wins =
  **0.449**, P(at least one) = **37.1%**

The current manuscript's 1.11 / 69.2% chance calculation therefore corresponds to the
**primary/session robot score field**, not to the best-of-panel featured scores. The
supplement keeps both definitions explicit so the paper can be corrected without changing
the underlying result.

## Privacy/anonymity

Per-session background answers are omitted. Only aggregate background tables are included.
Original UUIDs, nicknames, exact timestamps, deployment/site identifiers, repository URLs,
and production-only source/footer material are excluded from this compact data release.

See `integrity/ANONYMIZATION_SCAN.txt` and `integrity/SHA256SUMS.txt`.
