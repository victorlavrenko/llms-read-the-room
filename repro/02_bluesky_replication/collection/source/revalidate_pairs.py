#!/usr/bin/env python3
"""Revalidate Bluesky repeated-link candidates against structured post records.

This is a second-stage analysis pass. It does NOT use Jetstream and does NOT
need a Jetstream API key. It hydrates only the posts already present in an
existing discovery sample through the public Bluesky AppView.

Main goals:
  * distrust URL-looking text; trust structured link facets/external embeds;
  * deduplicate by the actual pair of post IDs, not by shared URL rows;
  * fetch mature engagement for every surviving historical candidate;
  * flag generic/dynamic targets and likely automation;
  * rank accounts by credible repeated-link behavior AND observable reposts.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

APPVIEW = "https://public.api.bsky.app/xrpc"
POST_COLLECTION = "app.bsky.feed.post"

SOCIAL_OR_GENERIC_DOMAINS = {
    "bsky.app", "x.com", "twitter.com", "facebook.com", "instagram.com",
    "threads.net", "t.me", "telegram.me", "whatsapp.com", "chat.whatsapp.com",
    "discord.com", "discord.gg", "mastodon.social", "mastodon.world",
    "linktr.ee",
}

SHORTENER_DOMAINS = {
    "t.co", "bit.ly", "tinyurl.com", "is.gd", "ow.ly", "buff.ly", "amzn.to",
    "lnkd.in", "shorturl.at",
}

TRACKING_KEYS = {
    "ref", "ref_", "referrer", "source", "src", "campaign", "mc_cid", "mc_eid",
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "si",
}

# Conservative signals that a URL identifies a changing dashboard/feed rather
# than a stable article/product/campaign/video. These are FLAGS, not automatic
# deletions, except for obvious profile/search/invite targets.
DYNAMIC_PATH_TOKENS = {
    "player", "scanner", "dashboard", "status", "statuses", "nowplaying",
    "now-playing", "weather", "forecast", "alerts", "alert", "earthquakes",
    "quake-info", "tracker", "tracking", "livemap", "live-map", "service",
    "schedule", "scores", "scoreboard",
}
GENERIC_PATH_TOKENS = {
    "profile", "profiles", "user", "users", "channel", "channels", "search",
    "feed", "feeds", "invite", "category", "categories", "tag", "tags",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_dir", help="existing crawl dir, e.g. smoke-weekago-3h")
    p.add_argument("--input-csv", default="filtered/clean_pairs.csv",
                   help="candidate CSV relative to input dir (default: filtered/clean_pairs.csv)")
    p.add_argument("--out", default=None,
                   help="output dir (default: <input_dir>/revalidated)")
    p.add_argument("--cache", default=None,
                   help="post-view cache JSONL (default: <out>/appview_posts.jsonl)")
    p.add_argument("--sleep", type=float, default=0.12,
                   help="delay after successful AppView batches (default: 0.12 sec)")
    p.add_argument("--min-gap-minutes", type=float, default=15.0)
    p.add_argument("--max-gap-hours", type=float, default=72.0)
    p.add_argument("--metadata-title-similarity", type=float, default=0.20,
                   help="flag same URL whose two external-card titles are very different")
    p.add_argument("--promising-reposts", type=int, default=3,
                   help="max repost count making an account PROMISING (default: 3)")
    p.add_argument("--possible-reposts", type=int, default=1,
                   help="max repost count making an account POSSIBLE (default: 1)")
    p.add_argument("--max-pairs-per-hour-automation", type=float, default=3.0,
                   help="flag very high repeated-link rate in discovery window")
    p.add_argument("--review-pairs-per-account", type=int, default=3)
    p.add_argument("--force-refresh", action="store_true",
                   help="ignore cached AppView post views and fetch again")
    return p.parse_args()


def parse_dt(s: str) -> datetime:
    s = (s or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def post_uri(did: str, rkey: str) -> str:
    return f"at://{did}/{POST_COLLECTION}/{rkey}"


def bsky_url(handle_or_did: str, rkey: str) -> str:
    return f"https://bsky.app/profile/{handle_or_did}/post/{rkey}"


def chunks(xs: list[str], n: int) -> Iterable[list[str]]:
    for i in range(0, len(xs), n):
        yield xs[i:i+n]


def api_json(endpoint: str, params: list[tuple[str, str]], sleep_s: float,
             retries: int = 6) -> dict[str, Any]:
    q = urllib.parse.urlencode(params)
    url = f"{APPVIEW}/{endpoint}?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "bsky-repeat-research/2.0"})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.load(r)
            if sleep_s > 0:
                time.sleep(sleep_s)
            return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
            ra = e.headers.get("Retry-After")
            delay = float(ra) if ra else min(2 ** attempt, 20)
            print(f"AppView HTTP {e.code}; retrying in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)
        except Exception as e:  # network reset, timeout, etc.
            last = e
            delay = min(2 ** attempt, 20)
            print(f"AppView error {e!r}; retrying in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)
    raise RuntimeError(f"AppView request failed after {retries} attempts: {last}")


def load_cache(path: Path) -> dict[str, dict[str, Any] | None]:
    out: dict[str, dict[str, Any] | None] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out[obj["uri"]] = obj.get("post")
    return out


def append_cache(path: Path, entries: list[tuple[str, dict[str, Any] | None]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for uri, post in entries:
            f.write(json.dumps({"uri": uri, "post": post}, ensure_ascii=False) + "\n")


def hydrate_posts(uris: list[str], cache_path: Path, sleep_s: float,
                  force_refresh: bool) -> dict[str, dict[str, Any] | None]:
    cache = {} if force_refresh else load_cache(cache_path)
    missing = [u for u in uris if u not in cache]
    print(f"Post views: {len(uris)} unique; cached={len(uris)-len(missing)}; to_fetch={len(missing)}")

    for bi, batch in enumerate(chunks(missing, 25), 1):
        data = api_json("app.bsky.feed.getPosts", [("uris", u) for u in batch], sleep_s)
        returned = {p.get("uri"): p for p in data.get("posts", []) if p.get("uri")}
        additions: list[tuple[str, dict[str, Any] | None]] = []
        for uri in batch:
            post = returned.get(uri)
            cache[uri] = post
            additions.append((uri, post))
        append_cache(cache_path, additions)
        if bi % 10 == 0 or bi == math.ceil(len(missing) / 25):
            print(f"  hydrated {min(bi*25, len(missing))}/{len(missing)} missing posts")
    return cache


def resolve_profiles(dids: list[str], sleep_s: float) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for batch in chunks(dids, 25):
        data = api_json("app.bsky.actor.getProfiles", [("actors", d) for d in batch], sleep_s)
        for p in data.get("profiles", []):
            did = p.get("did")
            if did:
                out[did] = p
    return out


def canonicalize_url(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        u = urllib.parse.urlsplit(raw if "://" in raw else "https://" + raw)
    except ValueError:
        return None
    host = (u.hostname or "").lower().rstrip(".")
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    port = u.port
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    path = u.path or "/"
    if path != "/":
        path = path.rstrip("/")

    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    clean_q = []
    for k, v in q:
        lk = k.lower()
        if lk.startswith("utm_") or lk in TRACKING_KEYS:
            continue
        # Amazon affiliate tag is tracking, not object identity.
        if host.endswith("amazon.com") and lk == "tag":
            continue
        clean_q.append((k, v))
    clean_q.sort()
    result = host + path
    if clean_q:
        result += "?" + urllib.parse.urlencode(clean_q, doseq=True)
    return result


def url_host_path(canon: str) -> tuple[str, str]:
    u = urllib.parse.urlsplit("https://" + canon)
    return (u.hostname or "").lower(), u.path or "/"


def extract_record_links(post: dict[str, Any]) -> set[str]:
    """Extract only structured outbound web links from the post record/view.

    Crucially, this never regexes post text. Bluesky's facet spec explicitly
    notes that visible URL text may be simplified/truncated while facet.uri is
    the complete destination.
    """
    out: set[str] = set()
    record = post.get("record") or {}

    # Current rich-text link facets.
    for facet in record.get("facets") or []:
        for feat in (facet or {}).get("features") or []:
            if (feat or {}).get("$type") == "app.bsky.richtext.facet#link":
                raw = feat.get("uri")
                c = canonicalize_url(raw) if raw else None
                if c:
                    out.add(c)

    # Deprecated but still structured entities.
    for ent in record.get("entities") or []:
        if (ent or {}).get("type") == "link":
            raw = ent.get("value")
            c = canonicalize_url(raw) if raw else None
            if c:
                out.add(c)

    def add_external(embed: Any) -> None:
        if not isinstance(embed, dict):
            return
        typ = embed.get("$type", "")
        if typ in ("app.bsky.embed.external", "app.bsky.embed.external#view") or "external" in embed:
            ext = embed.get("external")
            if isinstance(ext, dict):
                raw = ext.get("uri")
                c = canonicalize_url(raw) if raw else None
                if c:
                    out.add(c)
        if typ in ("app.bsky.embed.recordWithMedia", "app.bsky.embed.recordWithMedia#view") or "media" in embed:
            add_external(embed.get("media"))

    add_external(record.get("embed"))
    add_external(post.get("embed"))
    return out


def extract_external_cards(post: dict[str, Any]) -> dict[str, dict[str, str]]:
    cards: dict[str, dict[str, str]] = {}

    def add(embed: Any) -> None:
        if not isinstance(embed, dict):
            return
        typ = embed.get("$type", "")
        if typ in ("app.bsky.embed.external", "app.bsky.embed.external#view") or "external" in embed:
            ext = embed.get("external")
            if isinstance(ext, dict):
                c = canonicalize_url(ext.get("uri", ""))
                if c:
                    cards[c] = {
                        "title": str(ext.get("title") or ""),
                        "description": str(ext.get("description") or ""),
                    }
        if typ in ("app.bsky.embed.recordWithMedia", "app.bsky.embed.recordWithMedia#view") or "media" in embed:
            add(embed.get("media"))

    record = post.get("record") or {}
    add(record.get("embed"))
    add(post.get("embed"))
    return cards


def token_set(s: str) -> set[str]:
    return set(re.findall(r"[\w]+", (s or "").lower(), flags=re.UNICODE))


def jaccard(a: str, b: str) -> float:
    aa, bb = token_set(a), token_set(b)
    if not aa and not bb:
        return 1.0
    union = aa | bb
    return len(aa & bb) / len(union) if union else 1.0


def target_flags(canon: str, card_a: dict[str, str] | None,
                 card_b: dict[str, str] | None, title_threshold: float) -> list[str]:
    host, path = url_host_path(canon)
    flags: list[str] = []
    segments = [s.lower() for s in path.split("/") if s]
    segset = set(segments)

    if path in ("", "/"):
        flags.append("root_url")
    if host in SOCIAL_OR_GENERIC_DOMAINS:
        flags.append("social_or_chat_target")
    if host in SHORTENER_DOMAINS:
        flags.append("shortener")
    if segset & GENERIC_PATH_TOKENS:
        flags.append("generic_profile_search_feed_path")
    if segset & DYNAMIC_PATH_TOKENS:
        flags.append("dynamic_dashboard_path")
    if "radio" in host or "weather" in host:
        flags.append("dynamic_domain_hint")

    if card_a and card_b:
        ta, tb = card_a.get("title", "").strip(), card_b.get("title", "").strip()
        if ta and tb and ta != tb:
            sim = jaccard(ta, tb)
            if sim < title_threshold:
                flags.append("external_title_mismatch")
    return flags


def hard_target_reject(flags: list[str]) -> bool:
    # Dynamic hints are deliberately review flags; the hard rejects are targets
    # that very clearly do not denote a stable underlying object.
    hard = {"root_url", "social_or_chat_target", "generic_profile_search_feed_path"}
    return bool(hard.intersection(flags))


def choose_target(shared: set[str], cards_a: dict[str, dict[str, str]],
                  cards_b: dict[str, dict[str, str]], title_threshold: float) -> tuple[str, list[str]]:
    ranked: list[tuple[int, str, list[str]]] = []
    for u in shared:
        flags = target_flags(u, cards_a.get(u), cards_b.get(u), title_threshold)
        host, path = url_host_path(u)
        score = 0
        if not hard_target_reject(flags):
            score += 100
        if "dynamic_dashboard_path" not in flags and "dynamic_domain_hint" not in flags:
            score += 20
        if "external_title_mismatch" not in flags:
            score += 10
        score += min(len([s for s in path.split("/") if s]), 5)
        if "shortener" in flags:
            score -= 5
        ranked.append((score, u, flags))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    _, u, flags = ranked[0]
    return u, flags


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                fields.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        if not fields:
            f.write("")
            return
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def median(xs: list[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def main() -> int:
    args = parse_args()
    inp = Path(args.input_dir)
    csv_path = inp / args.input_csv
    if not csv_path.exists():
        raise SystemExit(f"Missing input CSV: {csv_path}")
    state_path = inp / "state.json"
    if not state_path.exists():
        raise SystemExit(f"Missing state.json: {state_path}")

    out = Path(args.out) if args.out else inp / "revalidated"
    out.mkdir(parents=True, exist_ok=True)
    cache_path = Path(args.cache) if args.cache else out / "appview_posts.jsonl"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    ws = parse_dt(state.get("window_start") or state.get("cutoff"))
    we = parse_dt(state.get("window_end")) if state.get("window_end") else ws + __import__("datetime").timedelta(days=float(state.get("days", 1)))
    window_hours = (we - ws).total_seconds() / 3600.0

    rows: list[dict[str, str]] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"Input candidate rows: {len(rows)}")

    # Deduplicate now by real pair identity. Multiple shared URLs in the old
    # pipeline must not create multiple observations.
    pair_map: dict[tuple[str, str, str], dict[str, str]] = {}
    for r in rows:
        did = r["did"]
        ar, br = r["a_rkey"], r["b_rkey"]
        key = (did, ar, br)
        pair_map.setdefault(key, r)
    base_pairs = list(pair_map.values())
    print(f"Unique actual post pairs before hydration: {len(base_pairs)}")

    uris = sorted({
        post_uri(r["did"], rk)
        for r in base_pairs
        for rk in (r["a_rkey"], r["b_rkey"])
    })
    posts = hydrate_posts(uris, cache_path, args.sleep, args.force_refresh)

    revalidated: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()

    for r in base_pairs:
        did = r["did"]
        au = post_uri(did, r["a_rkey"])
        bu = post_uri(did, r["b_rkey"])
        a, b = posts.get(au), posts.get(bu)
        reasons: list[str] = []

        if not a:
            reasons.append("a_missing_or_deleted")
        if not b:
            reasons.append("b_missing_or_deleted")
        if reasons:
            rr = dict(r); rr["revalidation_reasons"] = ";".join(reasons)
            rejected.append(rr); reason_counts.update(reasons); continue

        # AppView is authoritative for current author identity of the posts.
        adid = ((a.get("author") or {}).get("did") or "")
        bdid = ((b.get("author") or {}).get("did") or "")
        if adid != did or bdid != did:
            reasons.append("author_mismatch")

        try:
            at, bt = parse_dt(r["a_created_at"]), parse_dt(r["b_created_at"])
            gap_h = (bt - at).total_seconds() / 3600.0
        except Exception:
            gap_h = float(r.get("gap_hours") or 0)
            at = bt = ws
            reasons.append("timestamp_parse_error")
        if not (ws <= at <= we and ws <= bt <= we):
            reasons.append("created_at_outside_window")
        if gap_h * 60 < args.min_gap_minutes:
            reasons.append("gap_too_short")
        if gap_h > args.max_gap_hours:
            reasons.append("gap_too_long")

        links_a, links_b = extract_record_links(a), extract_record_links(b)
        shared = links_a & links_b
        if not shared:
            reasons.append("no_shared_structured_url")
            rr = dict(r)
            rr.update({
                "a_structured_urls": " | ".join(sorted(links_a)),
                "b_structured_urls": " | ".join(sorted(links_b)),
                "revalidation_reasons": ";".join(reasons),
            })
            rejected.append(rr); reason_counts.update(reasons); continue

        cards_a, cards_b = extract_external_cards(a), extract_external_cards(b)
        target, flags = choose_target(shared, cards_a, cards_b, args.metadata_title_similarity)
        if hard_target_reject(flags):
            reasons.append("no_specific_stable_target")

        ar = int(a.get("repostCount") or 0)
        br = int(b.get("repostCount") or 0)
        al = int(a.get("likeCount") or 0)
        bl = int(b.get("likeCount") or 0)
        aq = int(a.get("quoteCount") or 0)
        bq = int(b.get("quoteCount") or 0)
        arp = int(a.get("replyCount") or 0)
        brp = int(b.get("replyCount") or 0)
        winner = "A" if ar > br else "B" if br > ar else "TIE"
        handle = ((a.get("author") or {}).get("handle") or (b.get("author") or {}).get("handle") or did)

        rr: dict[str, Any] = dict(r)
        rr.update({
            "handle": handle,
            "a_uri": au,
            "b_uri": bu,
            "a_bsky_url": bsky_url(handle, r["a_rkey"]),
            "b_bsky_url": bsky_url(handle, r["b_rkey"]),
            "a_structured_urls": " | ".join(sorted(links_a)),
            "b_structured_urls": " | ".join(sorted(links_b)),
            "shared_structured_urls": " | ".join(sorted(shared)),
            "shared_structured_url_count": len(shared),
            "selected_target": target,
            "target_flags": ";".join(flags),
            "stable_target": int(not hard_target_reject(flags)
                                 and "dynamic_dashboard_path" not in flags
                                 and "dynamic_domain_hint" not in flags
                                 and "external_title_mismatch" not in flags),
            "a_external_title": (cards_a.get(target) or {}).get("title", ""),
            "b_external_title": (cards_b.get(target) or {}).get("title", ""),
            "a_reposts": ar, "b_reposts": br,
            "a_likes": al, "b_likes": bl,
            "a_quotes": aq, "b_quotes": bq,
            "a_replies": arp, "b_replies": brp,
            "repost_winner": winner,
            "winner_reposts": max(ar, br),
            "repost_margin": abs(ar - br),
            "total_reposts": ar + br,
            "measurable_repost_winner": int(ar != br),
            "revalidation_reasons": ";".join(reasons),
        })

        if reasons:
            rejected.append(rr); reason_counts.update(reasons)
        else:
            revalidated.append(rr)

    # Current profiles for every account with at least one structurally valid pair.
    dids = sorted({r["did"] for r in revalidated})
    print(f"Resolving {len(dids)} account profiles...")
    profiles = resolve_profiles(dids, args.sleep) if dids else {}

    by_author: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in revalidated:
        by_author[r["did"]].append(r)

    accounts: list[dict[str, Any]] = []
    for did, ps in by_author.items():
        p = profiles.get(did, {})
        handle = p.get("handle") or ps[0].get("handle") or did
        stable = [x for x in ps if int(x.get("stable_target", 0)) == 1]
        measurable = [x for x in stable if int(x.get("measurable_repost_winner", 0)) == 1]
        nonzero = [x for x in stable if int(x.get("winner_reposts", 0)) > 0]
        target_urls = {x.get("selected_target") for x in stable if x.get("selected_target")}
        max_rep = max([int(x.get("winner_reposts", 0)) for x in stable], default=0)
        total_rep = sum(int(x.get("total_reposts", 0)) for x in stable)
        max_margin = max([int(x.get("repost_margin", 0)) for x in stable], default=0)
        pair_rate = len(ps) / window_hours if window_hours > 0 else 0.0
        flags: list[str] = []
        if pair_rate > args.max_pairs_per_hour_automation:
            flags.append("high_repeat_rate_possible_automation")
        if ps and len(stable) / len(ps) < 0.5:
            flags.append("many_dynamic_or_ambiguous_targets")
        posts_count = int(p.get("postsCount") or 0)
        followers = int(p.get("followersCount") or 0)
        if posts_count > 50000 and followers < 500:
            flags.append("very_high_post_volume_low_followers")

        if stable and max_rep >= args.promising_reposts and measurable:
            status = "PROMISING"
        elif stable and max_rep >= args.possible_reposts:
            status = "POSSIBLE"
        elif stable:
            status = "LOW_ENGAGEMENT"
        else:
            status = "NO_STABLE_PAIR"

        accounts.append({
            "did": did,
            "handle": handle,
            "display_name": p.get("displayName", ""),
            "status": status,
            "followers": followers,
            "follows": int(p.get("followsCount") or 0),
            "posts_count": posts_count,
            "validated_pairs": len(ps),
            "stable_pairs": len(stable),
            "distinct_stable_targets": len(target_urls),
            "measurable_pairs": len(measurable),
            "nonzero_pairs": len(nonzero),
            "max_winner_reposts": max_rep,
            "max_repost_margin": max_margin,
            "total_reposts_across_stable_pairs": total_rep,
            "pairs_per_hour_in_discovery": round(pair_rate, 3),
            "flags": ";".join(flags),
        })

    status_order = {"PROMISING": 0, "POSSIBLE": 1, "LOW_ENGAGEMENT": 2, "NO_STABLE_PAIR": 3}
    accounts.sort(key=lambda x: (
        status_order.get(x["status"], 9),
        -int(x["measurable_pairs"]),
        -int(x["max_winner_reposts"]),
        -int(x["total_reposts_across_stable_pairs"]),
        -int(x["stable_pairs"]),
        -int(x["followers"]),
    ))
    for i, a in enumerate(accounts, 1):
        a["priority_rank"] = i

    rank_of = {a["did"]: a["priority_rank"] for a in accounts}
    status_of = {a["did"]: a["status"] for a in accounts}
    revalidated.sort(key=lambda r: (
        rank_of.get(r["did"], 10**9),
        -int(r.get("stable_target", 0)),
        -int(r.get("winner_reposts", 0)),
        -int(r.get("repost_margin", 0)),
    ))

    review: list[dict[str, Any]] = []
    for a in accounts:
        if a["status"] not in ("PROMISING", "POSSIBLE"):
            continue
        candidates = [r for r in revalidated if r["did"] == a["did"] and int(r.get("stable_target", 0)) == 1]
        candidates.sort(key=lambda r: (-int(r.get("winner_reposts", 0)), -int(r.get("repost_margin", 0))))
        seen_targets: set[str] = set()
        chosen: list[dict[str, Any]] = []
        for r in candidates:
            t = str(r.get("selected_target") or "")
            if t not in seen_targets:
                chosen.append(r); seen_targets.add(t)
            if len(chosen) >= args.review_pairs_per_account:
                break
        for r in candidates:
            if len(chosen) >= args.review_pairs_per_account:
                break
            if r not in chosen:
                chosen.append(r)
        for r in chosen:
            x = dict(r)
            x["account_status"] = a["status"]
            x["account_priority_rank"] = a["priority_rank"]
            x["account_followers"] = a["followers"]
            x["account_flags"] = a["flags"]
            review.append(x)

    write_csv(out / "revalidated_pairs.csv", revalidated)
    write_csv(out / "rejected_pairs.csv", rejected)
    write_csv(out / "watchlist_accounts.csv", accounts)
    write_csv(out / "manual_review.csv", review)

    # Convenience subset with an actual historical repost winner.
    measurable_rows = [r for r in revalidated
                       if int(r.get("stable_target", 0)) == 1
                       and int(r.get("measurable_repost_winner", 0)) == 1]
    measurable_rows.sort(key=lambda r: (-int(r.get("winner_reposts", 0)), -int(r.get("repost_margin", 0))))
    write_csv(out / "measurable_pairs.csv", measurable_rows)

    summary = {
        "source_csv": str(csv_path),
        "input_rows": len(rows),
        "unique_actual_post_pairs": len(base_pairs),
        "unique_posts_hydrated": len(uris),
        "revalidated_pairs": len(revalidated),
        "rejected_pairs": len(rejected),
        "stable_target_pairs": sum(int(r.get("stable_target", 0)) for r in revalidated),
        "measurable_stable_pairs": len(measurable_rows),
        "accounts_with_revalidated_pairs": len(accounts),
        "promising_accounts": sum(a["status"] == "PROMISING" for a in accounts),
        "possible_accounts": sum(a["status"] == "POSSIBLE" for a in accounts),
        "low_engagement_accounts": sum(a["status"] == "LOW_ENGAGEMENT" for a in accounts),
        "window_hours": window_hours,
        "rejection_counts": dict(reason_counts),
        "notes": [
            "URLs are extracted only from structured facets/external embeds; visible URL-looking text is ignored.",
            "Observations are deduplicated by actual post pair, regardless of how many URLs the pair shares.",
            "Dynamic target heuristics are conservative flags; only obvious root/social/profile/search/feed targets are hard-rejected.",
            "Account ranking prioritizes observed repost signal, then number of credible stable pairs; it no longer requires two repeated URLs in a 3-hour slice.",
        ],
    }
    (out / "revalidation_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nOutputs: {out}")
    print("  watchlist_accounts.csv     <- account ranking")
    print("  manual_review.csv          <- inspect these first")
    print("  measurable_pairs.csv       <- pairs with an actual repost winner")
    print("  revalidated_pairs.csv      <- all structurally valid pairs")
    print("  rejected_pairs.csv         <- audit trail")
    print("  revalidation_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
