# Beat the Robot — anonymized study protocol

## Primary task

Participants completed 20 binary judgments in Round 1. Each item showed two historical
tweets written by the same account and pointing to the same link. The participant chose
which tweet received more retweets. The alternatives were displayed as X and Y.

Participants could mark a judgment as a **Close call** in versions where that field had
been introduced. No model advice, item-level correctness, or live score was shown before
the primary Round-1 choices were complete.

Round 1 is the primary human analysis. An optional second 20-item round remained blind.
Item-level feedback was unlocked after Round 2; subsequent rounds were therefore
post-feedback learning rounds and are descriptive only.

## Consent and collected fields

The participant-facing consent text was:

> I consent to take part

> My choices, response times, and answers above may be stored for research.

The study stored binary choices, response times, and three coarse background variables:
native English, marketing experience, and early-Twitter use. No participant account
credentials were required. Identity was not authenticated.

## Version evolution relevant to analysis

The 70 completed Round-1 sessions span three recorded interface versions:
- 1 session: human-retweet-v1
- 10 sessions: human-retweet-v3
- 59 sessions: human-retweet-v4

Close-call metadata were not collected in the 11 earlier sessions. Accordingly, the
deidentified trial table contains **blank values**, not `0`, for those 220 judgments.
Model close-call analyses use only the 59 v4 sessions (1,180 trials).

One legacy v1 session predates the six-model per-item panel snapshot. It contributes to
the 70-session human-vs-session-robot game scoreline but is excluded from fixed six-model
matched analyses, which therefore use 69 sessions (62 for the model with incomplete
coverage).
