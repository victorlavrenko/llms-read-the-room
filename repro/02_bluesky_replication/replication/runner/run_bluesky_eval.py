#!/usr/bin/env python3
"""Fresh Bluesky replication of the historical Twitter retweet-prediction task.

This runner intentionally mirrors the user's earlier minimal Tan/HypoBench
experiment while changing only the platform-specific task wording.

Key safeguards:
- Model calls are constructed ONLY from data/questions.csv.
- data/answer_key.csv is never read until scoring/reporting.
- No repost counts, handles, target URLs, source URIs, or labels enter prompts.
- The 48 audited X/Y orientations are frozen; this script does not reshuffle them.
- All six contemporary models from the historical experiment are available
  through the same OpenRouter preset.

Outputs are append-only/resumable JSONL plus scored CSV/Markdown reports.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

VERSION = "1.0.0-bluesky-replication"

DEFAULT_MODELS = [
    "qwen/qwen3.7-flash",
    "deepseek/deepseek-v4-flash-0731",
    "google/gemini-3.7-flash",
    "qwen/qwen3.7-max",
    "openai/gpt-5.6-sol",
    "anthropic/claude-opus-4.8",
]

PROMPT_TEMPLATE = """These two Bluesky posts were published by the same account, about the same link, within 12 hours of each other.

Which one do you think received more reposts?

X:
{post_x}

Y:
{post_y}

You must choose either X or Y. Also indicate whether your choice was a close call.

Finish with exactly these two lines:

Prediction: X or Y
Close call: Yes or No"""


_PREDICTION_RE = re.compile(r"(?im)^\s*prediction\s*:\s*(?:post\s+)?([XY])\s*$")
_CLOSE_RE = re.compile(r"(?im)^\s*close\s+call\s*:\s*(yes|no)\s*$")
_MARKUP_RE = re.compile(r"[*_`]")

_FALLBACK_PATTERNS = [
    re.compile(
        r"(?is)\b(?:final\s+answer|answer|my\s+choice|choice|pick|winner)"
        r"\s*(?:is|=|:|-)?\s*(?:post\s+)?([XY])\b"
    ),
    re.compile(
        r"(?is)\bI(?:'d|\s+would)?\s*(?:guess|say|choose|pick|select|bet(?:\s+on)?)"
        r"\s+(?:post\s+)?([XY])\b"
    ),
    re.compile(
        r"(?is)\b(?:post\s+)?([XY])\s+"
        r"(?:(?:almost\s+)?certainly\s+|probably\s+|likely\s+|definitely\s+|clearly\s+)?"
        r"(?:got|gets|would\s+get|received|had)\s+(?:significantly\s+)?more\s+reposts\b"
    ),
    re.compile(r"(?is)\b(?:post\s+)?([XY])\s+(?:probably\s+|likely\s+)?wins?\b"),
    re.compile(r"(?is)\b(?:I\s+think|I\s+believe|my\s+guess\s+is)\s+(?:post\s+)?([XY])\b"),
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_questions(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    required = {"pair_id", "X", "Y"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"{path} must contain columns {sorted(required)}")
    ids = [r["pair_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("questions.csv contains duplicate pair_id values")
    for r in rows:
        if not r["X"].strip() or not r["Y"].strip():
            raise ValueError(f"blank X/Y text in pair {r['pair_id']}")
    return rows


def load_answer_key(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    required = {"pair_id", "correct_answer"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"{path} must contain columns {sorted(required)}")
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        if r["correct_answer"] not in {"X", "Y"}:
            raise ValueError(f"bad answer for {r['pair_id']}: {r['correct_answer']!r}")
        if r["pair_id"] in out:
            raise ValueError(f"duplicate pair_id in answer key: {r['pair_id']}")
        out[r["pair_id"]] = r
    return out


def make_prompt(row: dict[str, str]) -> str:
    # IMPORTANT: this function accepts a question row only.
    return PROMPT_TEMPLATE.format(post_x=row["X"], post_y=row["Y"])


def extract_answer(text: str | None) -> tuple[str | None, str | None, str | None, bool]:
    """Return prediction, close_call, extraction_method, strict_two_line_format."""
    if not text or not text.strip():
        return None, None, None, False

    normalized = _MARKUP_RE.sub("", text)
    pred_matches = list(_PREDICTION_RE.finditer(normalized))
    close_matches = list(_CLOSE_RE.finditer(normalized))

    pred = pred_matches[-1].group(1).upper() if pred_matches else None
    close = close_matches[-1].group(1).title() if close_matches else None

    if pred:
        # Strict format means the final two nonblank lines are precisely the requested markers.
        lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]
        strict = False
        if len(lines) >= 2:
            strict = (
                re.fullmatch(r"(?i)prediction\s*:\s*(?:post\s+)?[XY]", lines[-2]) is not None
                and re.fullmatch(r"(?i)close\s+call\s*:\s*(yes|no)", lines[-1]) is not None
            )
        return pred, close, "prediction_marker", strict

    candidates: list[tuple[int, str]] = []
    for pattern in _FALLBACK_PATTERNS:
        for m in pattern.finditer(normalized):
            candidates.append((m.start(1), m.group(1).upper()))
    if candidates:
        _, pred = max(candidates, key=lambda x: x[0])
        return pred, close, "explicit_fallback", False

    lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]
    if lines:
        m = re.fullmatch(r"(?i)(?:post\s+)?([XY])[.!]?", lines[-1])
        if m:
            return m.group(1).upper(), close, "bare_last_line", False

    return None, close, None, False


def get_client() -> Any:
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise RuntimeError("Missing dependency. Run: pip install -r requirements.txt") from exc
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("Set OPENROUTER_API_KEY before a live run.")
    return AsyncOpenAI(
        api_key=key,
        base_url="https://openrouter.ai/api/v1",
        timeout=180.0,
        max_retries=4,
        default_headers={
            "HTTP-Referer": "https://github.com/",
            "X-OpenRouter-Title": "Bluesky Social Transmission Replication",
        },
    )


def usage_dict(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    details = getattr(usage, "completion_tokens_details", None)
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "reasoning_tokens": getattr(details, "reasoning_tokens", None) if details else None,
    }


def get_reasoning_text(message: Any) -> str | None:
    for attr in ("reasoning", "reasoning_content"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value:
            return value
    details = getattr(message, "reasoning_details", None)
    if details:
        try:
            return json.dumps(details, ensure_ascii=False, default=str)
        except Exception:
            return str(details)
    return None


async def one_call(
    client: Any,
    sem: asyncio.Semaphore,
    model: str,
    question: dict[str, str],
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    # Only question data is present here; gold is intentionally unavailable.
    prompt = make_prompt(question)
    started = time.perf_counter()
    base = {
        "model": model,
        "pair_id": question["pair_id"],
        "post_x": question["X"],
        "post_y": question["Y"],
        "prompt": prompt,
    }
    try:
        async with sem:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        latency = time.perf_counter() - started
        if not getattr(response, "choices", None):
            raise RuntimeError("API returned no choices")
        msg = response.choices[0].message
        content = msg.content or ""
        pred, close, method, strict = extract_answer(content)
        failure = None if pred else ("empty_output" if not content.strip() else "parse_failure")
        return {
            **base,
            "raw_output": content,
            "raw_reasoning": get_reasoning_text(msg),
            "prediction_xy": pred,
            "close_call": close,
            "extraction_method": method,
            "strict_two_line_format": strict,
            "parsed": pred is not None,
            "failure_kind": failure,
            "latency_sec": round(latency, 4),
            **usage_dict(response),
            "error": None,
        }
    except Exception as exc:
        return {
            **base,
            "raw_output": None,
            "raw_reasoning": None,
            "prediction_xy": None,
            "close_call": None,
            "extraction_method": None,
            "strict_two_line_format": False,
            "parsed": False,
            "failure_kind": "call_error",
            "latency_sec": round(time.perf_counter() - started, 4),
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "reasoning_tokens": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for r in rows:
        key = (r["model"], r["pair_id"])
        if key not in latest:
            order.append(key)
        latest[key] = r
    return [latest[k] for k in order]


def wilson_interval(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return center - half, center + half


def binom_two_sided_p(k: int, n: int) -> float:
    if n == 0:
        return float("nan")
    lo = min(k, n - k)
    tail = sum(math.comb(n, i) for i in range(lo + 1)) / (2**n)
    return min(1.0, 2 * tail)


def score_rows(
    rows: list[dict[str, Any]],
    answer_key: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    scored = []
    for r in latest_rows(rows):
        rr = dict(r)
        gold = answer_key.get(r["pair_id"])
        if gold is None:
            raise ValueError(f"pair_id {r['pair_id']} missing from answer key")
        rr["gold_xy"] = gold["correct_answer"]
        pred = r.get("prediction_xy")
        rr["correct"] = (pred == gold["correct_answer"]) if pred in {"X", "Y"} else None
        # Keep outcome metadata only in scored artifacts, never prompts.
        for key in (
            "winner_reposts", "loser_reposts", "repost_margin",
            "winner_loser_ratio", "publication_gap_hours", "handle", "target"
        ):
            if key in gold:
                rr[key] = gold[key]
        scored.append(rr)
    return scored


def summarize(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in scored:
        grouped[r["model"]].append(r)

    out = []
    for model, rs in sorted(grouped.items()):
        valid = [r for r in rs if r.get("prediction_xy") in {"X", "Y"}]
        correct = sum(r.get("correct") is True for r in valid)
        n = len(valid)
        lo, hi = wilson_interval(correct, n)
        x_count = sum(r.get("prediction_xy") == "X" for r in valid)

        nonclose = [r for r in valid if r.get("close_call") == "No"]
        close = [r for r in valid if r.get("close_call") == "Yes"]
        nc_correct = sum(r.get("correct") is True for r in nonclose)
        c_correct = sum(r.get("correct") is True for r in close)

        out.append({
            "model": model,
            "attempted": len(rs),
            "valid": n,
            "correct": correct,
            "accuracy": correct / n if n else None,
            "ci95_low": lo if n else None,
            "ci95_high": hi if n else None,
            "p_vs_0.5": binom_two_sided_p(correct, n) if n else None,
            "failures": len(rs) - n,
            "pred_x_rate": x_count / n if n else None,
            "close_call_reported": len(close),
            "nonclose_reported": len(nonclose),
            "nonclose_coverage": len(nonclose) / n if n else None,
            "nonclose_accuracy": nc_correct / len(nonclose) if nonclose else None,
            "close_accuracy": c_correct / len(close) if close else None,
            "strict_format_rate": (
                sum(bool(r.get("strict_two_line_format")) for r in valid) / n if n else None
            ),
        })
    return out


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    if fields is None:
        fields = []
        seen = set()
        for r in rows:
            for k in r:
                if k not in seen:
                    seen.add(k)
                    fields.append(k)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def fmt_pct(x: float | None) -> str:
    return "NA" if x is None else f"{100*x:.1f}%"


def write_summary_md(path: Path, summary: list[dict[str, Any]]) -> None:
    lines = [
        "# Bluesky replication summary",
        "",
        "| Model | Valid | Accuracy | 95% CI | p vs .5 | Non-close acc. | Non-close coverage | X-rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summary:
        ci = "NA" if r["accuracy"] is None else f"{100*r['ci95_low']:.1f}–{100*r['ci95_high']:.1f}%"
        p = "NA" if r["p_vs_0.5"] is None else f"{r['p_vs_0.5']:.4g}"
        lines.append(
            f"| `{r['model']}` | {r['valid']} | {fmt_pct(r['accuracy'])} | {ci} | {p} | "
            f"{fmt_pct(r['nonclose_accuracy'])} | {fmt_pct(r['nonclose_coverage'])} | "
            f"{fmt_pct(r['pred_x_rate'])} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def validate_frozen_data(questions_path: Path, answer_key_path: Path) -> tuple[list[dict[str,str]], dict[str,dict[str,str]]]:
    questions = load_questions(questions_path)
    answers = load_answer_key(answer_key_path)
    qids = {r["pair_id"] for r in questions}
    aids = set(answers)
    if qids != aids:
        raise ValueError(f"Question/answer IDs differ: questions-only={qids-aids}, answers-only={aids-qids}")
    # This release is frozen to the audited 48-pair replication set.
    if len(questions) != 48:
        raise ValueError(f"Expected frozen 48-pair dataset, found {len(questions)}")
    return questions, answers


async def run(args: argparse.Namespace) -> int:
    questions_path = Path(args.questions)
    answer_key_path = Path(args.answer_key)
    questions, answers = validate_frozen_data(questions_path, answer_key_path)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    results_path = outdir / "raw_results.jsonl"

    models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else DEFAULT_MODELS
    if not models:
        raise ValueError("No models selected")

    manifest = {
        "version": VERSION,
        "questions": str(questions_path),
        "questions_sha256": sha256_file(questions_path),
        "answer_key": str(answer_key_path),
        "answer_key_sha256": sha256_file(answer_key_path),
        "n_pairs": len(questions),
        "models": models,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "concurrency": args.concurrency,
        "prompt_template": PROMPT_TEMPLATE,
        "system_prompt": None,
        "xy_orientation": "frozen_audited_input",
        "gold_loaded_into_model_calls": False,
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (outdir / "prompt_template.txt").write_text(PROMPT_TEMPLATE + "\n", encoding="utf-8")

    if args.dry_run:
        print(f"version={VERSION}")
        print(f"pairs={len(questions)} models={len(models)} total_calls={len(questions)*len(models)}")
        print(f"answer balance: X={sum(a['correct_answer']=='X' for a in answers.values())}, "
              f"Y={sum(a['correct_answer']=='Y' for a in answers.values())}")
        for q in questions[:args.dry_run_count]:
            print("=" * 80)
            print(f"pair_id={q['pair_id']} [gold intentionally not displayed]")
            print(make_prompt(q))
        return 0

    if args.score_only:
        raw = read_jsonl(results_path)
        scored = score_rows(raw, answers)
        summary = summarize(scored)
        write_csv(outdir / "scored_predictions.csv", scored)
        write_csv(outdir / "summary.csv", summary)
        write_summary_md(outdir / "summary.md", summary)
        print((outdir / "summary.md").read_text(encoding="utf-8"))
        return 0

    client = get_client()
    sem = asyncio.Semaphore(args.concurrency)
    existing = read_jsonl(results_path) if args.resume else []
    done = {
        (r["model"], r["pair_id"])
        for r in latest_rows(existing)
        if r.get("prediction_xy") in {"X", "Y"}
    }

    jobs = [
        (model, q)
        for model in models
        for q in questions
        if (model, q["pair_id"]) not in done
    ]
    print(f"version={VERSION}")
    print(f"pairs={len(questions)} models={len(models)} pending_calls={len(jobs)}")

    async def wrapped(model: str, q: dict[str,str]) -> dict[str,Any]:
        return await one_call(client, sem, model, q, args.temperature, args.max_tokens)

    completed = 0
    batch_size = args.batch_size
    total_batches = (len(jobs) + batch_size - 1) // batch_size if jobs else 0

    for batch_no, start in enumerate(range(0, len(jobs), batch_size), start=1):
        batch = jobs[start:start+batch_size]
        print(f"Launching batch {batch_no}/{total_batches}: {len(batch)} calls", flush=True)
        tasks = [asyncio.create_task(wrapped(model, q)) for model, q in batch]
        for task in asyncio.as_completed(tasks):
            r = await task
            append_jsonl(results_path, r)
            completed += 1
            status = "OK" if r.get("prediction_xy") else "FAIL"
            print(
                f"[{completed:>3}/{len(jobs)}] {status} {r['model']} pair={r['pair_id']} "
                f"pred={r.get('prediction_xy')} close={r.get('close_call')} "
                f"latency={r.get('latency_sec')}s",
                flush=True,
            )
            if args.debug and status == "FAIL":
                print(f"  error={r.get('error')!r} raw={r.get('raw_output')!r}", flush=True)

    all_raw = read_jsonl(results_path)
    scored = score_rows(all_raw, answers)
    summary = summarize(scored)
    write_csv(outdir / "scored_predictions.csv", scored)
    write_csv(outdir / "summary.csv", summary)
    write_summary_md(outdir / "summary.md", summary)

    print("\n" + (outdir / "summary.md").read_text(encoding="utf-8"))
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="48-pair fresh Bluesky social-transmission replication")
    p.add_argument("--questions", default="data/questions.csv")
    p.add_argument("--answer-key", default="data/answer_key.csv")
    p.add_argument("--models", default=None, help="Comma-separated OpenRouter model IDs; default is the historical six-model set.")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--concurrency", type=int, default=12)
    p.add_argument("--batch-size", type=int, default=48)
    p.add_argument("--outdir", default="runs/bluesky-48")
    p.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--dry-run-count", type=int, default=3)
    p.add_argument("--score-only", action="store_true")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        print("Interrupted. raw_results.jsonl is durable; rerun the same command to resume.")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
