# Source provenance

This directory contains the study-critical, non-identifying core of the deployed
`human-retweet-v4` experiment: experiment constants, the self-check prompt, deterministic
assignment logic, and scoring/featured-robot selection logic.

The files were verified against the frozen production source snapshot before packaging.
The deployed study implementation is public at `https://github.com/victorlavrenko/beat-the-robot`. The study snapshot corresponding to `human-retweet-v4` is commit `972ad75d923faf7e96ded1d40f6841e3fc33f6fe`. File-level SHA-256 digests for this compact snapshot are supplied in `integrity/SHA256SUMS.txt`.

The complete participant UI is not duplicated here because it is available in the public study repository at the commit above. This directory retains the assignment, prompt, and scoring logic required to interpret the frozen study data.
