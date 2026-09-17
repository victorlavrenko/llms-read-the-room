#!/usr/bin/env python3
"""One-command, code-only strengthening experiments for 'LLMs Read the Room Better Than Humans'.

Experiments:
1) Verify/reanalyse the frozen 1,142-pair historical benchmark.
2) Selected code-only live robustness checks: prompt wording, X/Y orientation, confidence, optional stability.
3) Optional automatic Bluesky mature-window discovery + evaluation.

The v1.2 default is intentionally focused: 50 pairs, Gemini/GPT/Claude, prompt + orientation + confidence, no stability, no new Bluesky crawl. All live LLM calls are resumable in JSONL. Gold labels are never placed in prompts.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request
import urllib.error
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = lambda *a, **k: None  # type: ignore

VERSION = "1.2.0"
ROOT = Path(__file__).resolve().parent


def log(message: str, level: str = "INFO") -> None:
    """Timestamped, immediately flushed progress output."""
    stamp = datetime.now().astimezone().strftime("%H:%M:%S")
    print(f"[{stamp}] [{level}] {message}", flush=True)


def log_stage(number: int, total: int, message: str) -> None:
    log(f"STEP {number}/{total} — {message}")

SNAPSHOT_COMMIT = "972ad75d923faf7e96ded1d40f6841e3fc33f6fe"
SNAPSHOT_URL = (
    "https://raw.githubusercontent.com/victorlavrenko/beat-the-robot/"
    f"{SNAPSHOT_COMMIT}/data/retweet_experiment_v1.private.json"
)
SNAPSHOT_SHA256 = "d656faca50d64790acbb0b908063eff6a8e7af81d9b3586a85ea275295b3825f"

PAPER_MODELS = [
    "google/gemini-3.7-flash",
    "openai/gpt-5.6-sol",
    "anthropic/claude-opus-4.8",
    "qwen/qwen3.7-max",
    "qwen/qwen3.7-flash",
    "deepseek/deepseek-v4-flash-0731",
]
# Fast, scientifically useful default panel. The two flaky/slow routes from the smoke
# test (Qwen Flash and DeepSeek) are not needed for the robustness appendix.
FOCUSED_MODELS = PAPER_MODELS[:3]
VALID_EXPERIMENTS = {"prompt_robustness", "orientation_swap", "confidence", "stability"}
DEFAULT_EXPERIMENTS = ["prompt_robustness", "orientation_swap", "confidence"]
DEFAULT_PROMPT_VARIANTS = ["paper_exact", "concise"]
OLDER_MODELS = [
    "anthropic/claude-3-haiku",
    "google/gemma-2-27b-it",
    "mistralai/mistral-large-2407",
    "qwen/qwen-2.5-72b-instruct",
]

HIST_PROMPTS = {
    "paper_exact": """These two tweets were posted by the same account, about the same link, in the early 2010s.

Which one do you think received more retweets at the time?

X:
{x}

Y:
{y}

You must choose either X or Y. Also indicate whether your choice was a close call.

Finish with exactly these two lines:

Prediction: X or Y
Close call: Yes or No""",
    "concise": """Two tweets below were posted by the same account about the same link in the early 2010s. Which one actually received more retweets?

X:
{x}

Y:
{y}

Choose X or Y. End with exactly:
Prediction: X or Y
Close call: Yes or No""",
    "reordered": """Predict the observed retweet winner between these two historical tweets. They were posted by the same account and linked to the same destination.

Tweet Y:
{y}

Tweet X:
{x}

Do not use tools or external lookup. Base the answer only on the text. End with exactly these two lines:
Prediction: X or Y
Close call: Yes or No""",
}

CONFIDENCE_PROMPT = """These two tweets were posted by the same account, about the same link, in the early 2010s.

Which one do you think received more retweets at the time?

X:
{x}

Y:
{y}

Choose X or Y and report your confidence that your chosen answer is correct as an integer from 50 to 100.
Do not use tools or external lookup. Finish with exactly these two lines:
Prediction: X or Y
Confidence: 50-100"""

BLUESKY_PROMPT = """These two Bluesky posts were published by the same account, about the same link, within 12 hours of each other.

Which one do you think received more reposts?

X:
{x}

Y:
{y}

You must choose either X or Y. Also indicate whether your choice was a close call.

Finish with exactly these two lines:

Prediction: X or Y
Close call: Yes or No"""

PRED_RE = re.compile(r"(?im)^\s*prediction\s*:\s*(?:post\s+|tweet\s+)?([XY])\s*$")
CLOSE_RE = re.compile(r"(?im)^\s*close\s+call\s*:\s*(yes|no)\s*$")
CONF_RE = re.compile(r"(?im)^\s*confidence\s*:\s*(100|[5-9]\d)\s*%?\s*$")
MARKUP_RE = re.compile(r"[*_`]")
FALLBACK_RE = re.compile(
    r"(?is)\b(?:final\s+answer|answer|my\s+choice|choice|pick|winner|prediction)"
    r"\s*(?:is|=|:|-)?\s*(?:post\s+|tweet\s+)?([XY])\b"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_models(value: str | None, default: list[str]) -> list[str]:
    if value:
        v=value.strip()
        if v.lower() in {"focused", "fast", "top3"}:
            return FOCUSED_MODELS[:]
        if v.lower() in {"all", "paper", "all6"}:
            return PAPER_MODELS[:]
        return [x.strip() for x in v.split(",") if x.strip()]
    return default[:]


def parse_experiments(value: str | None) -> list[str]:
    if not value or value.strip().lower() in {"focused", "default"}:
        return DEFAULT_EXPERIMENTS[:]
    if value.strip().lower() == "all":
        return ["prompt_robustness", "orientation_swap", "confidence", "stability"]
    xs=[x.strip() for x in value.split(",") if x.strip()]
    bad=[x for x in xs if x not in VALID_EXPERIMENTS]
    if bad:
        raise ValueError(f"Unknown experiment(s): {', '.join(bad)}. Valid: {', '.join(sorted(VALID_EXPERIMENTS))}")
    return xs


def parse_prompt_variants(value: str | None) -> list[str]:
    if not value:
        return DEFAULT_PROMPT_VARIANTS[:]
    if value.strip().lower() == "all":
        return list(HIST_PROMPTS)
    xs=[x.strip() for x in value.split(",") if x.strip()]
    bad=[x for x in xs if x not in HIST_PROMPTS]
    if bad:
        raise ValueError(f"Unknown prompt variant(s): {', '.join(bad)}. Valid: {', '.join(HIST_PROMPTS)}")
    if "paper_exact" not in xs:
        xs.insert(0,"paper_exact")
    return xs


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    z2 = z*z
    den = 1 + z2/n
    center = (p + z2/(2*n)) / den
    half = z * math.sqrt((p*(1-p) + z2/(4*n))/n) / den
    return center-half, center+half


def pct(x: float | None) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "NA"
    return f"{100*x:.1f}%"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k); fields.append(k)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        if not fields:
            return
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        f.flush()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                try: out.append(json.loads(ln))
                except json.JSONDecodeError: pass
    return out


def _snapshot_candidate_paths(explicit: str | None = None) -> list[Path]:
    """Likely locations of the private frozen benchmark on the user's machine."""
    rel = Path("data") / "retweet_experiment_v1.private.json"
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_path = os.getenv("RETWEET_SNAPSHOT")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(ROOT / rel)

    # The original project has commonly lived at ~/Code/beat-the-robot.  Derive
    # the Windows user root from this suite's location too, which works when the
    # suite is run from /mnt/c/Users/<name>/Downloads under WSL/Git Bash-like shells.
    roots = [Path.home()]
    roots.extend(list(ROOT.parents)[:5])
    repo_names = [
        Path("Code") / "beat-the-robot",
        Path("code") / "beat-the-robot",
        Path("Projects") / "beat-the-robot",
        Path("projects") / "beat-the-robot",
        Path("beat-the-robot"),
    ]
    for root in roots:
        for repo in repo_names:
            candidates.append(root / repo / rel)

    # Preserve order while removing duplicates.
    out: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        key = str(c.resolve(strict=False))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _accept_snapshot_bytes(data: bytes, destination: Path, source: str) -> dict[str, Any] | None:
    got = sha256_bytes(data)
    if got != SNAPSHOT_SHA256:
        log(f"Rejected snapshot from {source}: SHA-256 {got} != expected {SNAPSHOT_SHA256}", "WARN")
        return None
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as e:
        log(f"Rejected snapshot from {source}: invalid JSON ({e})", "WARN")
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    log(f"Historical snapshot verified and cached: {destination}")
    return obj


def _snapshot_from_git_repo(repo: Path, destination: Path) -> dict[str, Any] | None:
    if not (repo / ".git").exists() or not shutil.which("git"):
        return None
    spec = f"{SNAPSHOT_COMMIT}:data/retweet_experiment_v1.private.json"
    log(f"Trying pinned blob from local Git repository: {repo}")
    p = subprocess.run(
        ["git", "-C", str(repo), "show", spec],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if p.returncode != 0:
        msg = p.stderr.decode("utf-8", errors="replace").strip().splitlines()
        if msg:
            log(f"Local git show failed: {msg[-1]}", "WARN")
        return None
    return _accept_snapshot_bytes(p.stdout, destination, f"git:{repo}@{SNAPSHOT_COMMIT}")


def download_snapshot(path: Path, explicit_path: str | None = None) -> dict[str, Any]:
    """Resolve the frozen benchmark robustly.

    The benchmark file is private in the source repository, so unauthenticated
    raw.githubusercontent.com can return 404.  We first reuse a verified cache,
    then look for the user's existing beat-the-robot checkout, then try public
    raw access, authenticated GitHub CLI, and finally a noninteractive git clone.
    Every accepted copy must match the pinned SHA-256.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        got = sha256_file(path)
        if got == SNAPSHOT_SHA256:
            log(f"Using verified cached historical snapshot: {path}")
            return json.loads(path.read_text(encoding="utf-8"))
        log(f"Ignoring stale/incorrect cached snapshot at {path} (SHA-256 {got})", "WARN")

    candidates = _snapshot_candidate_paths(explicit_path)
    log(f"Looking for the pinned historical snapshot locally ({len(candidates)} candidate paths)...")
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            log(f"Found candidate snapshot: {candidate}")
            obj = _accept_snapshot_bytes(candidate.read_bytes(), path, str(candidate))
            if obj is not None:
                return obj

    # Try pinned history from nearby/local beat-the-robot repositories even if the
    # current working-tree copy is absent or changed.
    repo_candidates: list[Path] = []
    for candidate in candidates:
        # .../<repo>/data/file -> repo root
        try:
            repo_candidates.append(candidate.parent.parent)
        except Exception:
            pass
    seen_repos: set[str] = set()
    for repo in repo_candidates:
        key = str(repo.resolve(strict=False))
        if key in seen_repos:
            continue
        seen_repos.add(key)
        obj = _snapshot_from_git_repo(repo, path)
        if obj is not None:
            return obj

    # Public/raw access is cheap to try, but a private source file legitimately
    # returns HTTP 404 here.  Treat that as a fallback failure, not a fatal error.
    log(f"Trying unauthenticated pinned GitHub raw URL: {SNAPSHOT_URL}")
    try:
        req = urllib.request.Request(SNAPSHOT_URL, headers={"User-Agent": "readroom-strengthening-suite/1.1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        obj = _accept_snapshot_bytes(data, path, SNAPSHOT_URL)
        if obj is not None:
            return obj
    except urllib.error.HTTPError as e:
        log(f"GitHub raw returned HTTP {e.code}; this is expected when the frozen file is private.", "WARN")
    except Exception as e:
        log(f"GitHub raw download failed: {type(e).__name__}: {e}", "WARN")

    # If `gh` is authenticated, the contents API can retrieve a private file.
    if shutil.which("gh"):
        endpoint = f"/repos/victorlavrenko/beat-the-robot/contents/data/retweet_experiment_v1.private.json?ref={SNAPSHOT_COMMIT}"
        log("Trying authenticated GitHub CLI (`gh api`) for the private frozen file...")
        p = subprocess.run(
            ["gh", "api", "-H", "Accept: application/vnd.github.raw+json", endpoint],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if p.returncode == 0:
            obj = _accept_snapshot_bytes(p.stdout, path, "authenticated gh api")
            if obj is not None:
                return obj
        else:
            err = p.stderr.decode("utf-8", errors="replace").strip().splitlines()
            log(f"Authenticated gh lookup failed: {err[-1] if err else 'unknown error'}", "WARN")
    else:
        log("GitHub CLI (`gh`) not found; skipping authenticated API fallback.", "INFO")

    # Last automatic fallback: clone with existing Git credentials, noninteractively.
    if shutil.which("git"):
        cache_repo = path.parent / ".beat-the-robot-cache"
        if not (cache_repo / ".git").exists():
            log("Trying a noninteractive git clone using existing Git credentials...")
            p = subprocess.run(
                ["git", "clone", "--filter=blob:none", "--no-checkout",
                 "https://github.com/victorlavrenko/beat-the-robot.git", str(cache_repo)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if p.returncode != 0:
                err = p.stderr.decode("utf-8", errors="replace").strip().splitlines()
                log(f"Credentialed git clone failed: {err[-1] if err else 'unknown error'}", "WARN")
        obj = _snapshot_from_git_repo(cache_repo, path)
        if obj is not None:
            return obj

    searched = "\n".join(f"  - {p}" for p in candidates[:15])
    raise RuntimeError(
        "Could not obtain the pinned private historical snapshot. The old script failed because "
        "raw.githubusercontent.com returns 404 for this private file.\n\n"
        "Automatic local paths searched include:\n" + searched +
        "\n\nFastest fix: keep your existing beat-the-robot checkout at ~/Code/beat-the-robot, "
        "or run with --snapshot-path /path/to/retweet_experiment_v1.private.json, "
        "or set RETWEET_SNAPSHOT to that file. The file must match the pinned SHA-256."
    )


def pair_to_record(p: dict[str, Any]) -> dict[str, Any]:
    x_source = p["xSource"]
    y_source = "second" if x_source == "first" else "first"
    first = p["firstTweet"]
    second = p["secondTweet"]
    x = first if x_source == "first" else second
    y = first if y_source == "first" else second
    gold = "X" if p["goldSource"] == x_source else "Y"
    return {
        "pair_id": str(p["pairId"]),
        "first": first,
        "second": second,
        "x": x,
        "y": y,
        "x_source": x_source,
        "gold_source": p["goldSource"],
        "gold": gold,
        "robotPredictions": p.get("robotPredictions", {}),
        "robotCloseCalls": p.get("robotCloseCalls", {}),
    }


def frozen_baseline(obj: dict[str, Any], outdir: Path, models: list[str]) -> list[dict[str, Any]]:
    pairs = [pair_to_record(p) for p in obj["pairs"]]
    rows = []
    for model in models:
        valid = correct = close_n = close_correct = non_n = non_correct = pred_x = 0
        for p in pairs:
            pred = p["robotPredictions"].get(model)
            cc = p["robotCloseCalls"].get(model)
            if pred not in {"X","Y"}: continue
            valid += 1; correct += pred == p["gold"]; pred_x += pred == "X"
            if cc is True:
                close_n += 1; close_correct += pred == p["gold"]
            elif cc is False:
                non_n += 1; non_correct += pred == p["gold"]
        lo, hi = wilson(correct, valid)
        rows.append({
            "model": model, "valid": valid, "correct": correct,
            "accuracy": correct/valid if valid else None,
            "ci95_low": lo, "ci95_high": hi,
            "pred_x_rate": pred_x/valid if valid else None,
            "close_n": close_n,
            "close_accuracy": close_correct/close_n if close_n else None,
            "nonclose_n": non_n,
            "nonclose_accuracy": non_correct/non_n if non_n else None,
            "nonclose_minus_close_pp": (100*(non_correct/non_n - close_correct/close_n)) if close_n and non_n else None,
        })
    write_csv(outdir / "frozen_baseline_reanalysis.csv", rows)
    return rows


def parse_output(text: str | None) -> tuple[str | None, str | None, int | None, str | None]:
    if not text: return None, None, None, None
    s = MARKUP_RE.sub("", text)
    pm = list(PRED_RE.finditer(s)); cm = list(CLOSE_RE.finditer(s)); fm = list(CONF_RE.finditer(s))
    pred = pm[-1].group(1).upper() if pm else None
    close = cm[-1].group(1).title() if cm else None
    conf = int(fm[-1].group(1)) if fm else None
    method = "prediction_marker" if pred else None
    if not pred:
        hits = list(FALLBACK_RE.finditer(s))
        if hits:
            pred = hits[-1].group(1).upper(); method = "fallback"
    if not pred:
        lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
        if lines:
            m = re.fullmatch(r"(?i)(?:post\s+|tweet\s+)?([XY])[.!]?", lines[-1])
            if m: pred = m.group(1).upper(); method = "bare_last_line"
    return pred, close, conf, method


def canonical_source(pred: str | None, orientation: str, p: dict[str, Any]) -> str | None:
    if pred not in {"X","Y"}: return None
    x_source = p["x_source"]
    y_source = "second" if x_source == "first" else "first"
    if orientation == "frozen":
        return x_source if pred == "X" else y_source
    if orientation == "swapped":
        return y_source if pred == "X" else x_source
    return None


def job_id(parts: Iterable[Any]) -> str:
    raw = "|".join(str(x) for x in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass
class LiveJob:
    experiment: str
    model: str
    pair_id: str
    prompt: str
    gold: str
    orientation: str = "frozen"
    prompt_variant: str = "paper_exact"
    repetition: int = 0
    metadata: dict[str, Any] | None = None

    def id(self) -> str:
        return job_id((self.experiment, self.model, self.pair_id, self.orientation, self.prompt_variant, self.repetition))


class OpenRouterRunner:
    def __init__(self, outpath: Path, concurrency: int, temperature: float, max_tokens: int, attempts: int, progress_every: int = 1, request_timeout: float = 90.0):
        self.outpath = outpath
        self.concurrency = concurrency
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.attempts = attempts
        self.request_timeout = request_timeout
        self.progress_every = max(1, progress_every)
        self.sem = asyncio.Semaphore(concurrency)
        self.lock = asyncio.Lock()
        self._client = None

    def client(self):
        if self._client is not None: return self._client
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise RuntimeError("Install requirements.txt first") from e
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is required for live experiments")
        self._client = AsyncOpenAI(
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            timeout=self.request_timeout,
            # Our outer loop owns retries. Disabling SDK retries prevents a single
            # flaky provider request from silently blocking for many minutes.
            max_retries=0,
            default_headers={"HTTP-Referer":"https://github.com/", "X-OpenRouter-Title":"Read the Room strengthening suite"},
        )
        return self._client

    async def one(self, j: LiveJob) -> dict[str, Any]:
        last_err = None
        for attempt in range(1, self.attempts + 1):
            started = time.perf_counter()
            try:
                async with self.sem:
                    r = await self.client().chat.completions.create(
                        model=j.model,
                        messages=[{"role":"user","content":j.prompt}],
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )
                msg = r.choices[0].message
                text = msg.content or ""
                pred, close, conf, method = parse_output(text)
                row = {
                    "job_id": j.id(), "experiment": j.experiment, "model": j.model,
                    "pair_id": j.pair_id, "orientation": j.orientation,
                    "prompt_variant": j.prompt_variant, "repetition": j.repetition,
                    "gold": j.gold, "prediction": pred, "close_call": close,
                    "confidence": conf, "parse_method": method,
                    "parsed": pred in {"X","Y"}, "attempt": attempt,
                    "latency_sec": round(time.perf_counter()-started, 4),
                    "prompt": j.prompt, "raw_output": text,
                    "error": None,
                    "metadata": j.metadata or {},
                    "timestamp_utc": now_iso(),
                }
                if pred in {"X","Y"} and (j.experiment != "confidence" or conf is not None):
                    return row
                last_err = "parse_failure"
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
            if attempt < self.attempts:
                await asyncio.sleep(min(2**(attempt-1), 4))
        return {
            "job_id": j.id(), "experiment": j.experiment, "model": j.model,
            "pair_id": j.pair_id, "orientation": j.orientation,
            "prompt_variant": j.prompt_variant, "repetition": j.repetition,
            "gold": j.gold, "prediction": None, "close_call": None,
            "confidence": None, "parse_method": None, "parsed": False,
            "attempt": self.attempts, "latency_sec": None, "prompt": j.prompt,
            "raw_output": None, "error": last_err, "metadata": j.metadata or {},
            "timestamp_utc": now_iso(),
        }

    async def run(self, jobs: list[LiveJob]) -> list[dict[str, Any]]:
        existing = read_jsonl(self.outpath)
        latest = {r.get("job_id"): r for r in existing if r.get("job_id")}
        pending = [j for j in jobs if not (j.id() in latest and latest[j.id()].get("parsed") and (j.experiment != "confidence" or latest[j.id()].get("confidence") is not None))]
        log(f"Live LLM jobs: total={len(jobs)}, reusable={len(jobs)-len(pending)}, pending={len(pending)}, concurrency={self.concurrency}")
        if not pending:
            log("All requested live jobs are already cached; no API calls needed.")
        started_all = time.perf_counter()
        completed = 0

        async def wrapped(j: LiveJob):
            nonlocal completed
            r = await self.one(j)
            async with self.lock:
                append_jsonl(self.outpath, r)
            status = "OK" if r.get("parsed") else "FAIL"
            completed += 1
            if completed == 1 or completed == len(pending) or completed % self.progress_every == 0 or status == "FAIL":
                elapsed = max(time.perf_counter() - started_all, 1e-6)
                rate = completed / elapsed
                eta = (len(pending) - completed) / rate if rate > 0 else float("inf")
                msg=(
                    f"LLM [{completed}/{len(pending)}] {status} {j.experiment} | {j.model} | "
                    f"pair={j.pair_id} | {j.orientation}/{j.prompt_variant}/r{j.repetition} | "
                    f"{r.get('latency_sec')}s | ETA ~{eta/60:.1f} min"
                )
                if status == "FAIL":
                    err=str(r.get("error") or "unknown failure").replace("\n"," ")
                    if len(err) > 240: err=err[:237]+"..."
                    msg += f" | error={err}"
                log(msg)
            return r

        batch = max(50, self.concurrency*4)
        for i in range(0, len(pending), batch):
            await asyncio.gather(*(wrapped(j) for j in pending[i:i+batch]))
        rows = read_jsonl(self.outpath)
        latest = {r.get("job_id"): r for r in rows if r.get("job_id")}
        return [latest[j.id()] for j in jobs if j.id() in latest]


def make_historical_jobs(sample: list[dict[str, Any]], models: list[str], stability_n: int, repeats: int,
                         experiments: list[str], prompt_variants: list[str]) -> list[LiveJob]:
    selected=set(experiments)
    jobs: dict[str, LiveJob] = {}
    need_exact = bool(selected & {"prompt_robustness", "orientation_swap", "stability"})
    for model in models:
        for p in sample:
            if "prompt_robustness" in selected:
                variants=prompt_variants
            elif need_exact:
                # Orientation/stability need the original exact-prompt prediction as
                # a paired reference. Keep the historical job id so old cached calls
                # are reusable, but do not report it as prompt robustness unless selected.
                variants=["paper_exact"]
            else:
                variants=[]
            for variant in variants:
                tmpl=HIST_PROMPTS[variant]
                j = LiveJob("prompt_robustness", model, p["pair_id"], tmpl.format(x=p["x"],y=p["y"]), p["gold"], "frozen", variant, 0,
                            {"x_source":p["x_source"],"gold_source":p["gold_source"]})
                jobs[j.id()] = j
            if "orientation_swap" in selected:
                swapped_gold = "Y" if p["gold"] == "X" else "X"
                j = LiveJob("orientation_swap", model, p["pair_id"], HIST_PROMPTS["paper_exact"].format(x=p["y"],y=p["x"]), swapped_gold, "swapped", "paper_exact", 0,
                            {"x_source":p["x_source"],"gold_source":p["gold_source"]})
                jobs[j.id()] = j
            if "confidence" in selected:
                j = LiveJob("confidence", model, p["pair_id"], CONFIDENCE_PROMPT.format(x=p["x"],y=p["y"]), p["gold"], "frozen", "confidence", 0,
                            {"x_source":p["x_source"],"gold_source":p["gold_source"]})
                jobs[j.id()] = j
        if "stability" in selected:
            for p in sample[:min(stability_n, len(sample))]:
                for rep in range(1, repeats):
                    j = LiveJob("stability", model, p["pair_id"], HIST_PROMPTS["paper_exact"].format(x=p["x"],y=p["y"]), p["gold"], "frozen", "paper_exact", rep,
                                {"x_source":p["x_source"],"gold_source":p["gold_source"]})
                    jobs[j.id()] = j
    return list(jobs.values())


def accuracy_rows(rows: list[dict[str, Any]], group_fields: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any,...], list[dict[str,Any]]] = defaultdict(list)
    for r in rows:
        groups[tuple(r.get(k) for k in group_fields)].append(r)
    out=[]
    for key, rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
        valid=[r for r in rs if r.get("prediction") in {"X","Y"}]
        correct=sum(r["prediction"]==r["gold"] for r in valid)
        lo,hi=wilson(correct,len(valid))
        d={k:v for k,v in zip(group_fields,key)}
        d.update({"attempted":len(rs),"valid":len(valid),"correct":correct,
                  "accuracy": correct/len(valid) if valid else None,"ci95_low":lo,"ci95_high":hi,
                  "failure_rate":1-len(valid)/len(rs) if rs else None})
        out.append(d)
    return out


def analyze_historical_live(rows: list[dict[str, Any]], sample: list[dict[str,Any]], outdir: Path,
                            models:list[str], repeats:int, experiments:list[str], prompt_variants:list[str]) -> dict[str,Any]:
    pmap={p["pair_id"]:p for p in sample}
    selected=set(experiments)
    results: dict[str,Any]={}
    prompt=[r for r in rows if r.get("experiment")=="prompt_robustness"]

    if "prompt_robustness" in selected:
        prompt=[r for r in prompt if r.get("prompt_variant") in set(prompt_variants)]
        prompt_summary=accuracy_rows(prompt,["model","prompt_variant"])
        write_csv(outdir/"prompt_robustness.csv", prompt_summary)
        prompt_model=[]
        for model in models:
            sr=[r for r in prompt_summary if r["model"]==model and r["accuracy"] is not None]
            bypair=defaultdict(dict)
            for r in prompt:
                if r.get("model")==model and r.get("prediction") in {"X","Y"}:
                    bypair[r["pair_id"]][r.get("prompt_variant")]=r["prediction"]
            full=[v for v in bypair.values() if all(x in v for x in prompt_variants)]
            unanimous=sum(len(set(v[x] for x in prompt_variants))==1 for v in full)
            prompt_model.append({
                "model":model,"prompt_variants":len(sr),
                "min_accuracy":min((r["accuracy"] for r in sr),default=None),
                "max_accuracy":max((r["accuracy"] for r in sr),default=None),
                "accuracy_range_pp":100*(max(r["accuracy"] for r in sr)-min(r["accuracy"] for r in sr)) if sr else None,
                "complete_pairs":len(full),"unanimous_prediction_rate":unanimous/len(full) if full else None,
            })
        write_csv(outdir/"prompt_robustness_model_summary.csv",prompt_model)
        results["prompt_robustness"]=prompt_model

    if "orientation_swap" in selected:
        ori=[]
        exact=[r for r in rows if r.get("experiment")=="prompt_robustness" and r.get("prompt_variant")=="paper_exact"]
        for model in models:
            f={r["pair_id"]:r for r in exact if r["model"]==model and r.get("prediction") in {"X","Y"}}
            ss={r["pair_id"]:r for r in rows if r.get("experiment")=="orientation_swap" and r["model"]==model and r.get("prediction") in {"X","Y"}}
            ids=sorted(set(f)&set(ss))
            same=0; fc=sc=0; f_x=s_x=0
            for pid in ids:
                pp=pmap[pid]
                fs=canonical_source(f[pid]["prediction"],"frozen",pp)
                sws=canonical_source(ss[pid]["prediction"],"swapped",pp)
                same += fs==sws
                fc += f[pid]["prediction"]==f[pid]["gold"]
                sc += ss[pid]["prediction"]==ss[pid]["gold"]
                f_x += f[pid]["prediction"]=="X"; s_x += ss[pid]["prediction"]=="X"
            ori.append({"model":model,"paired_valid":len(ids),"canonical_prediction_consistency":same/len(ids) if ids else None,
                        "frozen_accuracy":fc/len(ids) if ids else None,"swapped_accuracy":sc/len(ids) if ids else None,
                        "swapped_minus_frozen_pp":100*(sc-fc)/len(ids) if ids else None,
                        "frozen_pred_x_rate":f_x/len(ids) if ids else None,"swapped_pred_x_rate":s_x/len(ids) if ids else None})
        write_csv(outdir/"orientation_swap.csv",ori); results["orientation_swap"]=ori

    if "confidence" in selected:
        conf=[]
        conf_rows=[r for r in rows if r.get("experiment")=="confidence"]
        for model in models:
            rs=[r for r in conf_rows if r["model"]==model and r.get("prediction") in {"X","Y"} and isinstance(r.get("confidence"),int)]
            if not rs:
                conf.append({"model":model,"valid":0}); continue
            ys=[1 if r["prediction"]==r["gold"] else 0 for r in rs]
            ps=[r["confidence"]/100 for r in rs]
            brier=sum((pp-y)**2 for pp,y in zip(ps,ys))/len(rs)
            ece=0.0
            for lo in [0.5,0.6,0.7,0.8,0.9]:
                hi=lo+0.1+1e-12
                inds=[i for i,pp in enumerate(ps) if lo <= pp < hi or (lo>=0.9 and pp<=1.0 and pp>=lo)]
                if inds:
                    ece += len(inds)/len(rs)*abs(sum(ps[i] for i in inds)/len(inds)-sum(ys[i] for i in inds)/len(inds))
            order=sorted(range(len(rs)),key=lambda i:ps[i],reverse=True)
            def topacc(frac:float)->float:
                n=max(1,math.ceil(len(rs)*frac)); idx=order[:n]; return sum(ys[i] for i in idx)/n
            conf.append({"model":model,"valid":len(rs),"accuracy":sum(ys)/len(rs),"mean_reported_confidence":sum(ps)/len(ps),
                         "brier_score":brier,"ece_5bins":ece,"top25pct_confidence_accuracy":topacc(.25),
                         "top50pct_confidence_accuracy":topacc(.50),"top75pct_confidence_accuracy":topacc(.75)})
        write_csv(outdir/"confidence_calibration.csv",conf); results["confidence"]=conf

    if "stability" in selected:
        stab=[]
        exact=[r for r in rows if r.get("experiment")=="prompt_robustness" and r.get("prompt_variant")=="paper_exact"]
        extra=[r for r in rows if r.get("experiment")=="stability"]
        eligible_ids={p["pair_id"] for p in sample[:min(len(sample), max(0, len(sample))) ]}
        for model in models:
            base={r["pair_id"]:r for r in exact if r["model"]==model and r.get("prediction") in {"X","Y"}}
            by=defaultdict(list)
            for pid,r in base.items():
                if pid in eligible_ids: by[pid].append(r)
            for r in extra:
                if r["model"]==model and r.get("prediction") in {"X","Y"}: by[r["pair_id"]].append(r)
            eligible=[v[:repeats] for v in by.values() if len(v)>=repeats]
            unanimous=sum(len({r["prediction"] for r in rs})==1 for rs in eligible)
            maj_correct=0; total_outputs=0; correct_outputs=0
            for rs in eligible:
                c=Counter(r["prediction"] for r in rs); modal=c.most_common(1)[0][0]
                maj_correct += modal==rs[0]["gold"]
                total_outputs += len(rs); correct_outputs += sum(r["prediction"]==r["gold"] for r in rs)
            stab.append({"model":model,"pairs_with_all_repeats":len(eligible),"repeats":repeats,
                         "unanimous_prediction_rate":unanimous/len(eligible) if eligible else None,
                         "modal_prediction_accuracy":maj_correct/len(eligible) if eligible else None,
                         "all_repeat_outputs_accuracy":correct_outputs/total_outputs if total_outputs else None})
        write_csv(outdir/"stochastic_stability.csv",stab); results["stability"]=stab
    return results


def run_cmd(cmd: list[str], cwd: Path | None = None, log_path: Path | None = None) -> None:
    log("RUN: " + " ".join(cmd))
    if cwd is not None:
        log(f"  cwd={cwd}")
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log(f"  subprocess output is also being saved to {log_path}")
        with log_path.open("a", encoding="utf-8") as f:
            p = subprocess.Popen(
                cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, universal_newlines=True,
            )
            assert p.stdout is not None
            for line in p.stdout:
                line = line.rstrip("\n")
                f.write(line + "\n"); f.flush()
                if line.strip():
                    log("subprocess | " + line)
            rc = p.wait()
    else:
        rc = subprocess.run(cmd, cwd=cwd).returncode
    if rc != 0:
        raise RuntimeError(f"Command failed ({rc}): {' '.join(cmd)}")


def fresh_bluesky_collect(work:Path,args:argparse.Namespace)->tuple[Path,Path,dict[str,Any]]:
    src=ROOT/"vendor"/"bluesky_collector"
    if not shutil.which("go"):
        raise RuntimeError("Go is required for automatic Bluesky discovery but was not found in PATH")
    crawl=work/"crawl"
    final=work/"final"
    crawl.mkdir(parents=True,exist_ok=True); final.mkdir(parents=True,exist_ok=True)
    # The exact production pattern from the paper's reproducibility bundle: mature window ending 8 days ago.
    run_cmd(["go","run",".","--hours",str(args.bluesky_hours),"--end-days-ago",str(args.bluesky_end_days_ago),
             "--max-gap-hours","12","--top","500","--buckets",str(args.bluesky_buckets),
             "--resolve-profiles=false","--progress-every","100000","--out",str(crawl)], cwd=src, log_path=work/"collector.log")
    run_cmd([sys.executable,str(src/"bluesky_finalize_pairs.py"),str(crawl),"--out",str(final),
             "--max-gap-hours","12","--min-age-days","7","--winner-min-reposts","8","--winner-min-margin","5",
             "--winner-min-ratio","2.0","--seed",str(args.seed)], cwd=ROOT, log_path=work/"finalizer.log")
    q=final/"llm_questions.csv"; a=final/"llm_answer_key.csv"
    if not q.exists() or not a.exists(): raise RuntimeError("Bluesky finalizer did not produce model-facing files")
    summary=json.loads((final/"final_summary.json").read_text(encoding="utf-8")) if (final/"final_summary.json").exists() else {}
    return q,a,summary


def make_bluesky_jobs(qpath:Path,apath:Path,current:list[str],older:list[str])->tuple[list[LiveJob],list[dict[str,str]],dict[str,dict[str,str]]]:
    qs=read_csv(qpath); ans={r["pair_id"]:r for r in read_csv(apath)}
    jobs=[]
    for model in current+older:
        for q in qs:
            gold=ans[q["pair_id"]]["correct_answer"]
            jobs.append(LiveJob("fresh_bluesky",model,q["pair_id"],BLUESKY_PROMPT.format(x=q["X"],y=q["Y"]),gold,"frozen","bluesky_exact",0,{}))
    return jobs,qs,ans


def bootstrap_panel_diff(case_diffs:list[float],seed:int,nboot:int=20000)->tuple[float,float,float]:
    if not case_diffs:return float("nan"),float("nan"),float("nan")
    rng=random.Random(seed); n=len(case_diffs); vals=[]
    for _ in range(nboot): vals.append(sum(case_diffs[rng.randrange(n)] for _ in range(n))/n)
    vals.sort(); lo=vals[int(.025*(nboot-1))]; hi=vals[int(.975*(nboot-1))]; pos=sum(v>0 for v in vals)/nboot
    return lo,hi,pos


def analyze_bluesky(rows:list[dict[str,Any]],current:list[str],older:list[str],outdir:Path,seed:int)->dict[str,Any]:
    summary=accuracy_rows(rows,["model"]); write_csv(outdir/"fresh_bluesky_models.csv",summary)
    valid_by_model={m:{r["pair_id"]:1 if r["prediction"]==r["gold"] else 0 for r in rows if r["model"]==m and r.get("prediction") in {"X","Y"}} for m in current+older}
    pair_ids=sorted({r["pair_id"] for r in rows})
    complete_current=[m for m in current if len(valid_by_model[m])==len(pair_ids)]
    complete_older=[m for m in older if len(valid_by_model[m])==len(pair_ids)]
    panel={"n_pairs":len(pair_ids),"complete_current_models":complete_current,"complete_older_models":complete_older}
    if pair_ids and complete_current and complete_older:
        case_diffs=[]
        for pid in pair_ids:
            cm=sum(valid_by_model[m][pid] for m in complete_current)/len(complete_current)
            om=sum(valid_by_model[m][pid] for m in complete_older)/len(complete_older)
            case_diffs.append(cm-om)
        diff=sum(case_diffs)/len(case_diffs); lo,hi,pos=bootstrap_panel_diff(case_diffs,seed)
        panel.update({"current_panel_accuracy":sum(sum(valid_by_model[m].values()) for m in complete_current)/(len(pair_ids)*len(complete_current)),
                      "older_panel_accuracy":sum(sum(valid_by_model[m].values()) for m in complete_older)/(len(pair_ids)*len(complete_older)),
                      "current_minus_older_pp":100*diff,"paired_bootstrap_ci95_low_pp":100*lo,"paired_bootstrap_ci95_high_pp":100*hi,
                      "bootstrap_positive_fraction":pos})
    (outdir/"fresh_bluesky_panel.json").write_text(json.dumps(panel,indent=2),encoding="utf-8")
    return {"model_summary":summary,"panel":panel}


def render_report(path:Path, manifest:dict[str,Any], frozen:list[dict[str,Any]], hist:dict[str,Any], bluesky:dict[str,Any]|None, bluesky_collect:dict[str,Any]|None, errors:list[str])->None:
    exps=set(manifest.get("historical_experiments",[]))
    lines=["# Automated strengthening report","",f"Generated: {manifest['generated_utc']}","", "## What was run","",
           "- Frozen 1,142-pair benchmark verification/reanalysis (all six paper models; no API calls)."]
    if "prompt_robustness" in exps: lines.append(f"- Prompt robustness across {len(manifest.get('prompt_variants',[]))} semantically equivalent task phrasings.")
    if "orientation_swap" in exps: lines.append("- X/Y orientation-swap robustness.")
    if "confidence" in exps: lines.append("- Probabilistic confidence/calibration and selective-accuracy analysis.")
    if "stability" in exps: lines.append("- Repeated-call stability under the same prompt.")
    if manifest.get("with_bluesky"): lines.append("- New mature-window Bluesky discovery/evaluation.")
    lines += ["", "## Frozen benchmark sanity check","", "| Model | Accuracy | 95% CI | Non-close acc. | Close acc. |", "|---|---:|---:|---:|---:|"]
    for r in frozen:
        lines.append(f"| `{r['model']}` | {pct(r['accuracy'])} | {pct(r['ci95_low'])}–{pct(r['ci95_high'])} | {pct(r['nonclose_accuracy'])} | {pct(r['close_accuracy'])} |")
    if "prompt_robustness" in exps:
        lines += ["","## Prompt robustness","", "| Model | Accuracy range across prompts | Unanimous predictions across selected prompts |", "|---|---:|---:|"]
        for r in hist.get("prompt_robustness",[]):
            rng="NA" if r.get("accuracy_range_pp") is None else f"{r['accuracy_range_pp']:.1f} pp"
            lines.append(f"| `{r['model']}` | {rng} | {pct(r.get('unanimous_prediction_rate'))} |")
    if "orientation_swap" in exps:
        lines += ["","## Orientation-swap robustness","", "| Model | Canonical-choice consistency | Original acc. | Swapped acc. | Δ swapped-original |", "|---|---:|---:|---:|---:|"]
        for r in hist.get("orientation_swap",[]):
            d="NA" if r.get("swapped_minus_frozen_pp") is None else f"{r['swapped_minus_frozen_pp']:+.1f} pp"
            lines.append(f"| `{r['model']}` | {pct(r.get('canonical_prediction_consistency'))} | {pct(r.get('frozen_accuracy'))} | {pct(r.get('swapped_accuracy'))} | {d} |")
    if "confidence" in exps:
        lines += ["","## Confidence calibration","", "| Model | Acc. | Mean confidence | Brier ↓ | ECE ↓ | Top-25% confident acc. |", "|---|---:|---:|---:|---:|---:|"]
        for r in hist.get("confidence",[]):
            if not r.get("valid"):
                lines.append(f"| `{r['model']}` | NA | NA | NA | NA | NA |"); continue
            lines.append(f"| `{r['model']}` | {pct(r.get('accuracy'))} | {pct(r.get('mean_reported_confidence'))} | {r['brier_score']:.3f} | {r['ece_5bins']:.3f} | {pct(r.get('top25pct_confidence_accuracy'))} |")
    if "stability" in exps:
        lines += ["","## Repeated-call stability","", "| Model | Pairs | Unanimous across repeats | Modal accuracy |", "|---|---:|---:|---:|"]
        for r in hist.get("stability",[]):
            lines.append(f"| `{r['model']}` | {r.get('pairs_with_all_repeats',0)} | {pct(r.get('unanimous_prediction_rate'))} | {pct(r.get('modal_prediction_accuracy'))} |")
    if bluesky_collect is not None:
        lines += ["","## Automatic fresh Bluesky replication","", f"Decisive pairs discovered: **{bluesky_collect.get('decisive_llm_pairs','?')}**.",""]
    if bluesky is not None:
        lines += ["| Model | Fresh accuracy | Valid |", "|---|---:|---:|"]
        for r in bluesky.get("model_summary",[]): lines.append(f"| `{r['model']}` | {pct(r.get('accuracy'))} | {r.get('valid',0)} |")
        pp=bluesky.get("panel",{})
        if pp.get("current_minus_older_pp") is not None:
            lines += ["",f"Complete current-model panel minus complete older-model panel: **{pp['current_minus_older_pp']:+.1f} pp**; paired bootstrap 95% CI **{pp['paired_bootstrap_ci95_low_pp']:+.1f} to {pp['paired_bootstrap_ci95_high_pp']:+.1f} pp**."]
    if errors:
        lines += ["","## Non-fatal issues","", *[f"- {e}" for e in errors]]
    lines += ["","## Interpretation guidance","",
              "These are robustness checks, not a replacement for the main 1,142-pair benchmark, contemporary human comparison, or the existing 48-pair fresh Bluesky replication. Report all selected variants and failures. Confidence here concerns external comparative prediction, not self-evaluation of generated prose.",""]
    path.write_text("\n".join(lines),encoding="utf-8")


async def main_async(args:argparse.Namespace)->int:
    load_dotenv(ROOT/".env"); load_dotenv()
    log(f"Read-the-Room strengthening suite v{VERSION}")
    log("This run is resumable: completed API calls are reused from JSONL state.")
    out=Path(args.out).resolve(); out.mkdir(parents=True,exist_ok=True)
    log(f"Output directory: {out}")
    current=parse_models(args.models or os.getenv("PAPER_MODELS"),FOCUSED_MODELS)
    older=parse_models(os.getenv("OLDER_MODELS"),OLDER_MODELS)
    experiments=parse_experiments(args.experiments)
    prompt_variants=parse_prompt_variants(args.prompt_variants)
    with_bluesky=bool(args.with_bluesky and not args.skip_bluesky)
    manifest={"version":VERSION,"generated_utc":now_iso(),"seed":args.seed,"historical_sample_n":args.sample_n,
              "stability_n":args.stability_n,"stability_repeats":args.repeats,"live_models":current,"frozen_models":PAPER_MODELS,"older_models":older,
              "historical_experiments":experiments,"prompt_variants":prompt_variants,"with_bluesky":with_bluesky,
              "temperature":args.temperature,"max_tokens":args.max_tokens,"concurrency":args.concurrency,"request_timeout":args.request_timeout,
              "snapshot_url":SNAPSHOT_URL,"snapshot_sha256":SNAPSHOT_SHA256}
    (out/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    log(f"Configuration: live_models={len(current)} ({', '.join(current)}); sample_n={args.sample_n}; experiments={','.join(experiments)}; prompt_variants={','.join(prompt_variants)}; concurrency={args.concurrency}; timeout={args.request_timeout}s; bluesky={'on' if with_bluesky else 'off'}")

    log_stage(1, 6, "Resolve and verify the frozen historical benchmark")
    obj=download_snapshot(out/"data"/"retweet_experiment_v1.private.json", args.snapshot_path)
    if obj.get("experimentVersion") != "human-retweet-v4" or len(obj.get("pairs",[])) != 1142:
        raise RuntimeError("Unexpected historical snapshot structure/version")
    log(f"Historical snapshot loaded: experimentVersion={obj.get('experimentVersion')}, pairs={len(obj.get('pairs', []))}")
    log_stage(2, 6, "Reanalyse frozen benchmark and build deterministic robustness sample")
    # Frozen scores are already in the snapshot, so report all six paper models
    # regardless of which smaller live robustness panel is selected.
    frozen=frozen_baseline(obj,out/"analysis",PAPER_MODELS)
    for r in frozen:
        log(f"Frozen baseline | {r['model']} | n={r['valid']} | accuracy={pct(r['accuracy'])}")
    all_pairs=[pair_to_record(p) for p in obj["pairs"]]
    # Stable prefix sampling: the first 50 items of an N=100 run are exactly the
    # same as an N=50 run, so smoke/focused runs can be reused when scaling up.
    def sample_rank(pp:dict[str,Any])->str:
        return hashlib.sha256(f"{args.seed}|{pp['pair_id']}".encode()).hexdigest()
    sample=sorted(all_pairs,key=sample_rank)[:min(args.sample_n,len(all_pairs))]
    sample.sort(key=lambda pp:int(pp["pair_id"]))
    write_csv(out/"data"/"historical_sample.csv",[{k:p[k] for k in ("pair_id","x","y","gold","x_source","gold_source")} for p in sample])
    log(f"Deterministic sample frozen: {len(sample)} pairs -> {out/'data'/'historical_sample.csv'}")

    errors=[]
    hist={}
    bluesky=None; bcollect=None
    if args.offline:
        log("--offline: skipping all live LLM calls and fresh collection")
    else:
        if not os.getenv("OPENROUTER_API_KEY"):
            raise RuntimeError("OPENROUTER_API_KEY is not set. Put it in .env or export it before running.")
        log_stage(3, 6, "Construct selected historical robustness jobs")
        runner=OpenRouterRunner(out/"raw"/"llm_calls.jsonl",args.concurrency,args.temperature,args.max_tokens,args.attempts,args.progress_every,args.request_timeout)
        jobs=make_historical_jobs(sample,current,args.stability_n,args.repeats,experiments,prompt_variants)
        counts=Counter(j.experiment for j in jobs)
        log("Historical live job counts: " + ", ".join(f"{k}={v}" for k,v in sorted(counts.items())) + f"; total={len(jobs)}")
        log_stage(4, 6, "Run/resume live LLM robustness experiments")
        rows=await runner.run(jobs)
        log("Live historical calls complete; computing analyses...")
        hist=analyze_historical_live(rows,sample,out/"analysis",current,args.repeats,experiments,prompt_variants)
        log(f"Historical analyses written to {out/'analysis'}")

        if with_bluesky:
            log_stage(5, 6, "Discover and evaluate a mature Bluesky window")
            try:
                log(f"Bluesky scan window: {args.bluesky_hours} hours ending {args.bluesky_end_days_ago} days ago. Collector output will stream here and to a log file.")
                q,a,bcollect=fresh_bluesky_collect(out/"fresh_bluesky",args)
                log(f"Bluesky discovery complete: decisive_llm_pairs={bcollect.get('decisive_llm_pairs', '?')}")
                if bcollect.get("decisive_llm_pairs",0) < args.min_bluesky_pairs:
                    errors.append(f"Fresh Bluesky scan produced only {bcollect.get('decisive_llm_pairs',0)} decisive pairs (< requested minimum {args.min_bluesky_pairs}); results are still reported descriptively.")
                bjobs,_,_=make_bluesky_jobs(q,a,current,older)
                log(f"Fresh Bluesky LLM jobs: {len(bjobs)} across {len(current)+len(older)} models")
                brows=await runner.run(bjobs)
                bluesky=analyze_bluesky([r for r in brows if r.get("experiment")=="fresh_bluesky"],current,older,out/"analysis",args.seed)
            except Exception as e:
                errors.append(f"Automatic fresh Bluesky replication did not complete: {type(e).__name__}: {e}. Historical robustness experiments are unaffected. See fresh_bluesky/*.log if present.")
                log(errors[-1], "WARN")
        else:
            log("Bluesky stage is off by default. Use --with-bluesky to run a new mature-window replication.")

    log_stage(6, 6, "Write consolidated report")
    render_report(out/"REPORT.md",manifest,frozen,hist,bluesky,bcollect,errors)
    log("DONE")
    log(f"Report: {out/'REPORT.md'}")
    return 0


def parse_args()->argparse.Namespace:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",default="runs/strengthening")
    p.add_argument("--snapshot-path",default=None,help="Optional path to retweet_experiment_v1.private.json. The suite also auto-detects likely local copies.")
    p.add_argument("--seed",type=int,default=20260917)
    p.add_argument("--sample-n",type=int,default=50,help="Historical pairs for live robustness tests (default 50; use 100 for the paper appendix).")
    p.add_argument("--models",default=None,help="Live model panel: focused/fast/top3 (default), all/all6, or comma-separated OpenRouter model IDs.")
    p.add_argument("--experiments",default="focused",help="focused (default: prompt_robustness,orientation_swap,confidence), all, or a comma-separated subset.")
    p.add_argument("--prompt-variants",default=None,help="Prompt robustness variants (default paper_exact,concise; use all to include reordered).")
    p.add_argument("--stability-n",type=int,default=25,help="Subset for repeated identical calls if stability is explicitly selected.")
    p.add_argument("--repeats",type=int,default=3,help="Total identical-prompt calls per stability item, including the base call.")
    p.add_argument("--temperature",type=float,default=0.0)
    p.add_argument("--max-tokens",type=int,default=1200)
    p.add_argument("--concurrency",type=int,default=12,help="Parallel OpenRouter calls (default 12).")
    p.add_argument("--attempts",type=int,default=2,help="Outer attempts per job (default 2; SDK retries are disabled).")
    p.add_argument("--request-timeout",type=float,default=90.0,help="Per-attempt API timeout in seconds (default 90).")
    p.add_argument("--progress-every",type=int,default=1,help="Print live LLM progress every N completed jobs (default every job).")
    p.add_argument("--with-bluesky",action="store_true",help="Also run a new mature-window Bluesky replication. Off by default because the existing 48-pair replication already addresses freshness.")
    p.add_argument("--skip-bluesky",action="store_true",help=argparse.SUPPRESS)  # v1.1 compatibility
    p.add_argument("--bluesky-hours",type=int,default=84)
    p.add_argument("--bluesky-end-days-ago",type=int,default=8)
    p.add_argument("--bluesky-buckets",type=int,default=32)
    p.add_argument("--min-bluesky-pairs",type=int,default=30)
    p.add_argument("--offline",action="store_true",help="Only verify/reanalyse frozen data; no APIs.")
    return p.parse_args()


def main()->None:
    try: raise SystemExit(asyncio.run(main_async(parse_args())))
    except KeyboardInterrupt:
        log("Interrupted. JSONL state is durable; rerun the same command to resume.", "WARN"); raise SystemExit(130)

if __name__=="__main__": main()
