# Automated strengthening report

Generated: 2026-09-17T16:46:31.524450+00:00

## What was run

- Frozen 1,142-pair benchmark verification/reanalysis (all six paper models; no API calls).
- Prompt robustness across 2 semantically equivalent task phrasings.
- X/Y orientation-swap robustness.
- Probabilistic confidence/calibration and selective-accuracy analysis.

## Frozen benchmark sanity check

| Model | Accuracy | 95% CI | Non-close acc. | Close acc. |
|---|---:|---:|---:|---:|
| `google/gemini-3.7-flash` | 79.7% | 77.3%–81.9% | 84.7% | 66.1% |
| `openai/gpt-5.6-sol` | 76.7% | 74.2%–79.1% | 79.8% | 62.2% |
| `anthropic/claude-opus-4.8` | 70.8% | 68.1%–73.4% | 90.5% | 64.3% |
| `qwen/qwen3.7-max` | 70.8% | 68.0%–73.3% | 73.4% | 54.2% |
| `qwen/qwen3.7-flash` | 64.7% | 61.9%–67.4% | 65.9% | 44.3% |
| `deepseek/deepseek-v4-flash-0731` | 63.5% | 60.7%–66.2% | 69.8% | 59.9% |

## Prompt robustness

| Model | Accuracy range across prompts | Unanimous predictions across selected prompts |
|---|---:|---:|
| `google/gemini-3.7-flash` | 3.0 pp | 93.0% |
| `openai/gpt-5.6-sol` | 7.0 pp | 83.0% |
| `anthropic/claude-opus-4.8` | 1.0 pp | 87.0% |

## Orientation-swap robustness

| Model | Canonical-choice consistency | Original acc. | Swapped acc. | Δ swapped-original |
|---|---:|---:|---:|---:|
| `google/gemini-3.7-flash` | 90.0% | 79.0% | 83.0% | +4.0 pp |
| `openai/gpt-5.6-sol` | 88.0% | 73.0% | 79.0% | +6.0 pp |
| `anthropic/claude-opus-4.8` | 80.0% | 75.0% | 73.0% | -2.0 pp |

## Confidence calibration

| Model | Acc. | Mean confidence | Brier ↓ | ECE ↓ | Top-25% confident acc. |
|---|---:|---:|---:|---:|---:|
| `google/gemini-3.7-flash` | 80.0% | 74.1% | 0.151 | 0.099 | 92.0% |
| `openai/gpt-5.6-sol` | 77.0% | 72.6% | 0.162 | 0.061 | 96.0% |
| `anthropic/claude-opus-4.8` | 74.0% | 67.8% | 0.180 | 0.086 | 96.0% |

## Interpretation guidance

These are robustness checks, not a replacement for the main 1,142-pair benchmark, contemporary human comparison, or the existing 48-pair fresh Bluesky replication. Report all selected variants and failures. Confidence here concerns external comparative prediction, not self-evaluation of generated prose.
