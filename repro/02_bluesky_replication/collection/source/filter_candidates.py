#!/usr/bin/env python3
"""
Filter Bluesky repeated-link discovery output WITHOUT re-crawling Jetstream.

Input directory must contain:
  candidate_pairs.csv
  state.json

Optional enrichment uses only Bluesky's public AppView:
  --resolve-profiles
  --fetch-engagement
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


SOCIAL_OR_GENERIC_DOMAINS = {
    "t.me",
    "telegram.me",
    "chat.whatsapp.com",
    "whatsapp.com",
    "bsky.app",
    "x.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "threads.net",
    "discord.gg",
    "discord.com",
    "linktr.ee",
}

SHORTENER_DOMAINS = {
    "t.co",
    "bit.ly",
    "tinyurl.com",
    "is.gd",
    "ow.ly",
    "buff.ly",
    "amzn.to",
}

PAIR_COLUMNS = [
    "did", "handle", "canonical_url",
    "a_rkey", "a_created_at", "a_text",
    "b_rkey", "b_created_at", "b_text",
    "gap_hours", "text_jaccard",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Filter repeated-link candidate pairs from an existing Bluesky crawl."
    )
    p.add_argument("input_dir", help="crawl output directory, e.g. smoke-weekago-3h")
    p.add_argument("--out", default=None,
                   help="output directory (default: <input_dir>/filtered)")
    p.add_argument("--min-gap-minutes", type=float, default=15.0,
                   help="minimum time between pair members (default: 15)")
    p.add_argument("--max-gap-hours", type=float, default=72.0,
                   help="maximum time between pair members (default: 72)")
    p.add_argument("--min-distinct-urls", type=int, default=2,
                   help="minimum different repeated targets for an account to count as habitual (default: 2)")
    p.add_argument("--min-clean-pairs", type=int, default=2,
                   help="minimum clean historical pairs for a qualified account (default: 2)")
    p.add_argument("--max-account-pairs-per-hour", type=float, default=3.0,
                   help="flag/exclude extremely high-rate accounts as likely automation (default: 3)")
    p.add_argument("--max-single-url-share", type=float, default=0.80,
                   help="flag accounts where one URL supplies more than this fraction of clean pairs (default: .80)")
    p.add_argument("--allow-social-domains", action="store_true",
                   help="do not reject links to social/chat platforms")
    p.add_argument("--resolve-profiles", action="store_true",
                   help="resolve qualified DIDs to current handles/display names")
    p.add_argument("--fetch-engagement", action="store_true",
                   help="fetch current repost/like/reply/quote counts for qualified pair posts")
    p.add_argument("--min-winner-reposts", type=int, default=0,
                   help="when engagement is fetched, additionally write pairs whose winner has at least N reposts")
    p.add_argument("--review-pairs-per-account", type=int, default=3,
                   help="representative pairs/account in review_sample.csv (default: 3)")
    p.add_argument("--sleep", type=float, default=0.15,
                   help="delay between public AppView requests (default: 0.15 sec)")
    return p.parse_args()


def dt(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def url_parts(canonical: str) -> tuple[str, str]:
    # crawler stores canonical URLs without a scheme
    raw = canonical.strip()
    u = urllib.parse.urlsplit(raw if "://" in raw else "https://" + raw)
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = u.path or "/"
    return host, path


def post_uri(did: str, rkey: str) -> str:
    return f"at://{did}/app.bsky.feed.post/{rkey}"


def api_json(endpoint: str, params: list[tuple[str, str]], sleep_s: float,
             retries: int = 5) -> dict:
    q = urllib.parse.urlencode(params)
    url = f"https://public.api.bsky.app/xrpc/{endpoint}?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "bsky-repeat-research/1.0"})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            if sleep_s:
                time.sleep(sleep_s)
            return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = e.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(2 ** attempt, 15)
            print(f"AppView HTTP {e.code}; retrying in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)
        except Exception as e:
            last = e
            time.sleep(min(2 ** attempt, 15))
    raise RuntimeError(f"AppView request failed after {retries} attempts: {last}")


def chunks(xs: list[str], n: int):
    for i in range(0, len(xs), n):
        yield xs[i:i+n]


def resolve_profiles(dids: list[str], sleep_s: float) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for batch in chunks(dids, 25):
        data = api_json(
            "app.bsky.actor.getProfiles",
            [("actors", did) for did in batch],
            sleep_s,
        )
        for p in data.get("profiles", []):
            out[p["did"]] = {
                "handle": p.get("handle", ""),
                "display_name": p.get("displayName", ""),
                "followers": p.get("followersCount", 0),
                "follows": p.get("followsCount", 0),
                "posts_count": p.get("postsCount", 0),
            }
    return out


def fetch_posts(uris: list[str], sleep_s: float) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for idx, batch in enumerate(chunks(uris, 25), 1):
        data = api_json(
            "app.bsky.feed.getPosts",
            [("uris", uri) for uri in batch],
            sleep_s,
        )
        for p in data.get("posts", []):
            author = p.get("author", {})
            out[p["uri"]] = {
                "reposts": p.get("repostCount", 0),
                "likes": p.get("likeCount", 0),
                "replies": p.get("replyCount", 0),
                "quotes": p.get("quoteCount", 0),
                "handle": author.get("handle", ""),
                "display_name": author.get("displayName", ""),
            }
        if idx % 20 == 0:
            print(f"Fetched engagement batches: {idx}")
    return out


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        seen = []
        have = set()
        for row in rows:
            for k in row:
                if k not in have:
                    have.add(k)
                    seen.append(k)
        fields = seen
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def median_or_zero(xs):
    return statistics.median(xs) if xs else 0.0


def main() -> int:
    args = parse_args()
    inp = Path(args.input_dir)
    out = Path(args.out) if args.out else inp / "filtered"
    out.mkdir(parents=True, exist_ok=True)

    state = json.loads((inp / "state.json").read_text(encoding="utf-8"))
    start = dt(state["window_start"])
    end = dt(state["window_end"])
    window_hours = (end - start).total_seconds() / 3600.0
    if window_hours <= 0:
        raise RuntimeError("invalid state window")

    raw: list[dict] = []
    with (inp / "candidate_pairs.csv").open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw.append(row)

    clean: list[dict] = []
    rejected: list[dict] = []
    rejection_counts = Counter()

    for row in raw:
        reasons = []
        try:
            a_time = dt(row["a_created_at"])
            b_time = dt(row["b_created_at"])
            gap_h = float(row["gap_hours"])
            jaccard = float(row.get("text_jaccard") or 0.0)
        except Exception:
            reasons.append("parse_error")
            a_time = b_time = start
            gap_h = 0.0
            jaccard = 0.0

        host, path = url_parts(row.get("canonical_url", ""))
        root_target = path in ("", "/")
        social_target = host in SOCIAL_OR_GENERIC_DOMAINS
        shortener = host in SHORTENER_DOMAINS

        if not (start <= a_time <= end and start <= b_time <= end):
            reasons.append("created_at_outside_window")
        if gap_h * 60.0 < args.min_gap_minutes:
            reasons.append("gap_too_short")
        if gap_h > args.max_gap_hours:
            reasons.append("gap_too_long")
        if root_target:
            reasons.append("generic_root_url")
        if social_target and not args.allow_social_domains:
            reasons.append("social_or_chat_target")

        enriched = dict(row)
        enriched.update({
            "domain": host,
            "path": path,
            "is_shortener": int(shortener),
            "a_uri": post_uri(row["did"], row["a_rkey"]),
            "b_uri": post_uri(row["did"], row["b_rkey"]),
            "filter_reasons": ";".join(reasons),
        })

        if reasons:
            rejected.append(enriched)
            rejection_counts.update(reasons)
        else:
            clean.append(enriched)

    # Account-level statistics from pair-level-clean candidates.
    by_author: dict[str, list[dict]] = defaultdict(list)
    for row in clean:
        by_author[row["did"]].append(row)

    account_rows: list[dict] = []
    qualified_dids = set()

    for did, rows in by_author.items():
        urls = [r["canonical_url"] for r in rows]
        url_counts = Counter(urls)
        distinct_urls = len(url_counts)
        pair_count = len(rows)
        pairs_per_hour = pair_count / window_hours
        gaps = [float(r["gap_hours"]) for r in rows]
        max_url_share = max(url_counts.values()) / pair_count if pair_count else 1.0

        flags = []
        if pairs_per_hour > args.max_account_pairs_per_hour:
            flags.append("very_high_pair_rate")
        if pair_count >= 4 and max_url_share > args.max_single_url_share:
            flags.append("single_url_dominates")
        if any(int(r["is_shortener"]) for r in rows):
            flags.append("contains_shortener")

        habitual = (
            pair_count >= args.min_clean_pairs
            and distinct_urls >= args.min_distinct_urls
        )
        automation_flag = "very_high_pair_rate" in flags
        qualified = habitual and not automation_flag

        if qualified:
            qualified_dids.add(did)

        account_rows.append({
            "did": did,
            "status": "QUALIFIED" if qualified else ("REVIEW" if habitual else "INSUFFICIENT_HISTORY"),
            "clean_pairs": pair_count,
            "distinct_repeated_urls": distinct_urls,
            "pairs_per_hour_in_discovery": round(pairs_per_hour, 3),
            "median_gap_minutes": round(median_or_zero(gaps) * 60, 2),
            "largest_single_url_share": round(max_url_share, 3),
            "flags": ";".join(flags),
            "example_url": rows[0]["canonical_url"] if rows else "",
            "handle": "",
            "display_name": "",
            "followers": "",
            "posts_count": "",
        })

    account_rows.sort(
        key=lambda r: (
            r["status"] != "QUALIFIED",
            -int(r["distinct_repeated_urls"]),
            -int(r["clean_pairs"]),
        )
    )

    qualified_pairs = [r for r in clean if r["did"] in qualified_dids]

    # Optional profile resolution.
    profiles = {}
    if args.resolve_profiles and qualified_dids:
        print(f"Resolving {len(qualified_dids)} qualified account profiles...")
        profiles = resolve_profiles(sorted(qualified_dids), args.sleep)
        for a in account_rows:
            p = profiles.get(a["did"])
            if p:
                a.update(p)

    # Optional current engagement. Only fetch posts surviving both filter levels.
    postdata = {}
    if args.fetch_engagement and qualified_pairs:
        uris = sorted({
            uri
            for r in qualified_pairs
            for uri in (r["a_uri"], r["b_uri"])
        })
        print(f"Fetching current engagement for {len(uris)} unique qualified posts...")
        postdata = fetch_posts(uris, args.sleep)

        for r in qualified_pairs:
            a = postdata.get(r["a_uri"], {})
            b = postdata.get(r["b_uri"], {})
            ar, br = a.get("reposts", ""), b.get("reposts", "")
            r["a_reposts"] = ar
            r["b_reposts"] = br
            r["a_likes"] = a.get("likes", "")
            r["b_likes"] = b.get("likes", "")
            r["a_replies"] = a.get("replies", "")
            r["b_replies"] = b.get("replies", "")
            r["a_quotes"] = a.get("quotes", "")
            r["b_quotes"] = b.get("quotes", "")
            if isinstance(ar, int) and isinstance(br, int):
                if ar > br:
                    r["repost_winner"] = "A"
                elif br > ar:
                    r["repost_winner"] = "B"
                else:
                    r["repost_winner"] = "TIE"
                r["winner_reposts"] = max(ar, br)
                r["repost_margin"] = abs(ar - br)
            else:
                r["repost_winner"] = ""
                r["winner_reposts"] = ""
                r["repost_margin"] = ""

            # getPosts already returns basic current author identity.
            if not profiles:
                p = a or b
                if p:
                    r["handle"] = p.get("handle", r.get("handle", ""))

    # Review sample: representative clean pairs for every qualified account.
    review = []
    for a in account_rows:
        if a["status"] != "QUALIFIED":
            continue
        rows = [r for r in qualified_pairs if r["did"] == a["did"]]
        # Diversity first: one pair per URL before a second pair from same URL.
        chosen = []
        seen_urls = set()
        for r in sorted(rows, key=lambda x: float(x["gap_hours"]), reverse=True):
            if r["canonical_url"] not in seen_urls:
                chosen.append(r)
                seen_urls.add(r["canonical_url"])
            if len(chosen) >= args.review_pairs_per_account:
                break
        if len(chosen) < args.review_pairs_per_account:
            for r in rows:
                if r not in chosen:
                    chosen.append(r)
                if len(chosen) >= args.review_pairs_per_account:
                    break
        for r in chosen:
            rr = dict(r)
            rr["account_status"] = a["status"]
            rr["account_distinct_repeated_urls"] = a["distinct_repeated_urls"]
            rr["account_clean_pairs"] = a["clean_pairs"]
            rr["account_flags"] = a["flags"]
            if a.get("handle"):
                rr["handle"] = a["handle"]
            review.append(rr)

    write_csv(out / "clean_pairs.csv", clean)
    write_csv(out / "qualified_pairs.csv", qualified_pairs)
    write_csv(out / "rejected_pairs.csv", rejected)
    write_csv(out / "ranked_accounts_filtered.csv", account_rows)
    write_csv(out / "review_sample.csv", review)

    if args.fetch_engagement and args.min_winner_reposts > 0:
        measurable = [
            r for r in qualified_pairs
            if isinstance(r.get("winner_reposts"), int)
            and r["winner_reposts"] >= args.min_winner_reposts
        ]
        write_csv(out / "qualified_pairs_measurable.csv", measurable)

    summary = {
        "input_pairs": len(raw),
        "pair_level_clean": len(clean),
        "pair_level_rejected": len(rejected),
        "clean_accounts": len(by_author),
        "qualified_habitual_accounts": len(qualified_dids),
        "qualified_pairs": len(qualified_pairs),
        "window_hours": window_hours,
        "rules": {
            "min_gap_minutes": args.min_gap_minutes,
            "max_gap_hours": args.max_gap_hours,
            "reject_root_urls": True,
            "reject_social_or_chat_targets": not args.allow_social_domains,
            "min_distinct_repeated_urls_per_account": args.min_distinct_urls,
            "min_clean_pairs_per_account": args.min_clean_pairs,
            "max_account_pairs_per_hour": args.max_account_pairs_per_hour,
            "max_single_url_share_flag": args.max_single_url_share,
        },
        "rejection_counts": dict(rejection_counts),
        "engagement_fetched": args.fetch_engagement,
    }
    (out / "filter_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nOutputs written to: {out}")
    print("Primary files:")
    print("  ranked_accounts_filtered.csv")
    print("  review_sample.csv")
    print("  qualified_pairs.csv")
    print("  filter_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
