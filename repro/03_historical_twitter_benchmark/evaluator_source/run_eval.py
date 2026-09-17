#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from retweet_eval import __version__
from retweet_eval.config import DEFAULT_ROUTES, RouteSpec
from retweet_eval.report import write_reports
from retweet_eval.scheduler import AdaptiveRunner
from retweet_eval.storage import Database
from retweet_eval.workspace import init_workspace


def load_routes(path: str | None) -> list[RouteSpec]:
    if not path:
        return list(DEFAULT_ROUTES)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [RouteSpec(**row) for row in data["routes"]]


def write_default_routes(path: Path) -> None:
    path.write_text(
        json.dumps({"routes": [asdict(route) for route in DEFAULT_ROUTES]}, indent=2),
        encoding="utf-8",
    )


async def main_async(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    if (workspace / "state.sqlite3").exists():
        db = Database(workspace / "state.sqlite3")
    elif args.init:
        db = init_workspace(workspace)
    else:
        raise RuntimeError(
            f"{workspace} is not a v6 workspace. For the current v5 run, first run migrate_v5.py."
        )

    if args.reopen_failed:
        reopened = db.reopen_failed()
        print(f"Reopened {reopened} exhausted cells", flush=True)

    routes_file = workspace / "routes.json"
    if not routes_file.exists():
        write_default_routes(routes_file)
    routes = load_routes(args.routes or str(routes_file))

    if args.status:
        progress = db.progress()
        write_reports(workspace, db.result_rows(), db.attempt_rows(), progress)
        print(json.dumps(progress, indent=2))
        print((workspace / "results" / "summary.md").read_text(encoding="utf-8"))
        db.close()
        return 0

    prompt = db.get_meta("prompt_template")
    print(f"retweet_llm_eval version={__version__}")
    print(f"workspace={workspace}")
    print("Existing progress:", json.dumps(db.progress()), flush=True)
    print(f"Prompt preserved from workspace manifest ({len(prompt)} chars).", flush=True)

    runner = AdaptiveRunner(
        workspace=workspace,
        db=db,
        prompt_template=prompt,
        routes=routes,
        max_tokens=args.max_tokens,
        truncation_max_tokens=args.retry_max_tokens,
        max_attempts=args.max_attempts,
        global_concurrency=args.global_concurrency,
        openrouter_concurrency=args.openrouter_concurrency,
        hf_concurrency=args.hf_concurrency,
        timeout=args.timeout,
        progress_every=args.progress_every,
        report_every=args.report_every,
        heartbeat_seconds=args.heartbeat,
    )
    try:
        await runner.run()
    finally:
        db.close()
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Adaptive multi-model retweet-prediction runner")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--workspace", default="runs/full-test-v6")
    p.add_argument("--init", action="store_true", help="Create a fresh full-test workspace (not for migration).")
    p.add_argument("--routes", default=None, help="Optional routes.json override.")
    p.add_argument("--status", action="store_true")
    p.add_argument("--reopen-failed", action="store_true")
    p.add_argument("--max-tokens", type=int, default=8192)
    p.add_argument("--retry-max-tokens", type=int, default=16384,
                   help="Ceiling used on retry attempts, including imported truncations.")
    p.add_argument("--max-attempts", type=int, default=5,
                   help="Total attempts per cell INCLUDING attempts imported from v5.")
    p.add_argument("--global-concurrency", type=int, default=80)
    p.add_argument("--openrouter-concurrency", type=int, default=64)
    p.add_argument("--hf-concurrency", type=int, default=32)
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--heartbeat", type=float, default=15.0)
    p.add_argument("--progress-every", type=int, default=10)
    p.add_argument("--report-every", type=int, default=100)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(asyncio.run(main_async(args)))
    except KeyboardInterrupt:
        print("Interrupted. SQLite state is durable; rerun the same command to resume.")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
