# Data dictionary

## `data/round1_trials.csv`

One row per primary Round-1 judgment (1,400 rows).

- `session`: sequential pseudonym S001–S070; no original session/participant ID is retained.
- `position`: 1–20 within the round.
- `pair_id`: frozen benchmark pair identifier.
- `player_prediction`: X or Y.
- `player_correct`: 1/0.
- `player_close_call`: 1/0 when collected; **blank means the field did not yet exist**.
- `response_time_ms`: recorded item response time.
- `legacy_primary_robot_*`: primary robot fields stored with the completion.
- `<model>_prediction`, `<model>_correct`, `<model>_close_call`: contemporaneous six-model
  panel data where available. Blank means unavailable/not collected, never “false”.

## `data/round1_sessions.csv`

One row per completed primary session. The table deliberately excludes nicknames,
original IDs, timestamps, and per-session background variables.

`primary_robot_score` is the frozen robot score stored for the session.
`comparison_robot_score` is the best-of-panel featured score where available, with the
single legacy session falling back to its primary robot. These are distinct quantities.

## Aggregate tables

- `model_aggregate_scores.csv`: fixed-model matched accuracy and session W/T/L.
- `disagreement_analysis.csv`: model correctness when player and model disagree.
- `close_call_analysis.csv`: model close/non-close accuracy on recorded metadata.
- `human_close_call_summary.csv`: sparse human close-call usage.
- `round_aggregates.csv`: descriptive Round 1/2/3 aggregates.
- `session_profile_aggregates.csv`: background variables in aggregate only.
- `chance_calibration.csv`: random-choice calibration under explicitly named comparison.
- `participant_authenticity_sensitivity.csv`: aggregate timing/sensitivity checks only.
