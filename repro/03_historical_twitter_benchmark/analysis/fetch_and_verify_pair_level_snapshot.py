#!/usr/bin/env python3
"""Fetch the frozen 1,142-pair study snapshot from the public Beat the Robot repo.

The snapshot contains pair text, gold/X-Y orientation, six model predictions, and model
close-call flags. It is pinned to the exact human-retweet-v4 study commit and checked by
SHA-256 before analysis.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

COMMIT = "972ad75d923faf7e96ded1d40f6841e3fc33f6fe"
URL = (
    "https://raw.githubusercontent.com/victorlavrenko/beat-the-robot/"
    f"{COMMIT}/data/retweet_experiment_v1.private.json"
)
EXPECTED_SHA256 = "d656faca50d64790acbb0b908063eff6a8e7af81d9b3586a85ea275295b3825f"
MODELS = [
    "google/gemini-3.7-flash",
    "openai/gpt-5.6-sol",
    "anthropic/claude-opus-4.8",
    "qwen/qwen3.7-max",
    "qwen/qwen3.7-flash",
    "deepseek/deepseek-v4-flash-0731",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="retweet_experiment_v1.private.json")
    args = ap.parse_args()
    out = Path(args.out)

    print(f"Downloading pinned snapshot: {URL}")
    data = urllib.request.urlopen(URL, timeout=60).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit(f"SHA-256 mismatch: {digest} != {EXPECTED_SHA256}")
    out.write_bytes(data)
    print(f"SHA-256 PASS: {digest}")

    obj = json.loads(data)
    assert obj["experimentVersion"] == "human-retweet-v4"
    pairs = obj["pairs"]
    assert len(pairs) == 1142

    print("\nPAIR-LEVEL MODEL CHECK")
    for model in MODELS:
        valid = correct = close = nonclose_valid = nonclose_correct = 0
        for p in pairs:
            pred = p["robotPredictions"].get(model)
            cc = p["robotCloseCalls"].get(model)
            if pred not in {"X", "Y"}:
                continue
            gold = "X" if p["goldSource"] == p["xSource"] else "Y"
            is_correct = pred == gold
            valid += 1
            correct += is_correct
            if cc is True:
                close += 1
            elif cc is False:
                nonclose_valid += 1
                nonclose_correct += is_correct
        print(
            f"{model}: {correct}/{valid}={100*correct/valid:.2f}% | "
            f"close={close} | non-close={nonclose_correct}/{nonclose_valid}="
            f"{100*nonclose_correct/nonclose_valid:.2f}%"
        )

    expected = {
        "google/gemini-3.7-flash": (910, 1142),
        "openai/gpt-5.6-sol": (876, 1142),
        "anthropic/claude-opus-4.8": (809, 1142),
        "qwen/qwen3.7-max": (808, 1142),
        "qwen/qwen3.7-flash": (739, 1142),
        "deepseek/deepseek-v4-flash-0731": (725, 1142),
    }
    for model, (exp_correct, exp_valid) in expected.items():
        got_valid = got_correct = 0
        for p in pairs:
            pred = p["robotPredictions"].get(model)
            if pred not in {"X", "Y"}:
                continue
            gold = "X" if p["goldSource"] == p["xSource"] else "Y"
            got_valid += 1
            got_correct += pred == gold
        assert (got_correct, got_valid) == (exp_correct, exp_valid)
    print("\nPASS: pair-level model counts match the frozen benchmark manifest.")


if __name__ == "__main__":
    main()
