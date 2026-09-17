# Prompt, order, and confidence robustness checks

This component archives the focused strengthening run added on 17 September 2026. It is a robustness diagnostic for the historical Twitter benchmark, not a replacement for the frozen 1,142-pair result.

## Frozen design

- Deterministic sample: 100 pair IDs selected by sorting `1..1142` by `SHA256("20260917|<pair_id>")` and taking the first 100.
- Models: `google/gemini-3.7-flash`, `openai/gpt-5.6-sol`, `anthropic/claude-opus-4.8`.
- Prompt robustness: paper-exact prompt plus one concise semantically equivalent prompt.
- Orientation robustness: rerun the paper-exact prompt with X and Y reversed and map predictions back to the underlying tweet identity.
- Confidence: choose X/Y and report an integer confidence from 50 to 100.
- Fresh chat context per condition; no tools; no system prompt; temperature 0; `max_tokens=1200`.
- Runner: OpenRouter, explicit parsing, hidden SDK retries disabled, two outer attempts.

The raw JSONL contains earlier smoke-test calls as well as the final 100-pair run. Reproduction selects exactly the deterministic 100-pair set and the latest record for each job ID.

## Paper-reported results

Prompt accuracy ranges across the two phrasings are 79--82% (Gemini), 73--80% (GPT-5.6 Sol), and 74--75% (Claude); canonical predictions agree across prompts on 93%, 83%, and 87% of pairs respectively.

After reversing X/Y, canonical-choice consistency is 90%, 88%, and 80%. Swapped-order accuracy is 83%, 79%, and 73%.

In the explicit-confidence condition, overall accuracy is 80%, 77%, and 74%; the most-confident quartile reaches 92%, 96%, and 96% respectively.

## Frozen-result reproduction

No API key is needed:

```bash
python repro/05_robustness_checks/analysis/reproduce_robustness_results.py
```

The script recomputes the paper statistics from `raw/llm_calls.jsonl` and checks them against the archived CSV outputs.

## Optional live rerun

The exact runner source is preserved under `runner/`. A future hosted-model rerun is not expected to be byte-identical because model endpoints and routing can change. The archived raw outputs are the scientific record.
