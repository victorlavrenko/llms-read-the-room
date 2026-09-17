#!/usr/bin/env python3
"""Finalize a fresh Bluesky repeated-link validation sample from an existing Jetstream crawl.

No Jetstream replay is performed. The script:
  1) precision-prefilters the already-downloaded candidate_pairs.csv offline;
  2) hydrates only surviving posts via the public Bluesky AppView getPosts endpoint;
  3) trusts structured facets/external embeds, never URL-looking display text;
  4) requires same author, same structured destination, materially different copy,
     <= 12 h between posts, and mature outcomes;
  5) excludes obvious mutable/dashboard/social/adult targets;
  6) freezes a decisive-outcome subset for LLM evaluation:
       winner >= 8 reposts, absolute margin >= 5, winner >= 2x loser.

The script is resumable: fetched post views are cached as JSONL.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import math
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
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
ADULT_RE = re.compile(
    r"\b(?:porn|porno|xxx|hentai|nsfw|fetish|bdsm|cuckold|deepthroat|milf|"
    r"big[-_ ]?tits?|big[-_ ]?ass|lesbian[-_ ]?porn|gaycruising|brazzers|"
    r"flirtydeals|justfor\.fans|onlyfans|oldwomantube|gaggingtube|disgracetube|"
    r"sexcam|adult|mym\.fans|fansly|viewfans|boobs?|topless|erotic)\b",
    re.I,
)
ADULT_DOMAINS = {
    "mym.fans", "onlyfans.com", "fansly.com", "justfor.fans", "flirtydeals.com",
    "brazzers.com", "disgracetube.com", "gaggingtube.com", "oldwomantube.com",
}
GENERIC_SERVICE_RULES = {
    "twitch.tv", "ko-fi.com", "paypal.me", "line.me", "patreon.com",
}
URLISH_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
MENTION_TAG_RE = re.compile(r"(?<!\w)[@#][\w.-]+", re.UNICODE)


def args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_dir", help="crawl directory containing candidate_pairs.csv and state.json")
    p.add_argument("--out", default=None, help="default: <input_dir>/llm-final")
    p.add_argument("--cache", default=None, help="post-view JSONL cache; default: <out>/appview_posts.jsonl")
    p.add_argument("--prefilter-only", action="store_true", help="stop before public AppView hydration")
    p.add_argument("--offline", action="store_true", help="use cache only; fail if required posts are not cached")
    p.add_argument("--sleep", type=float, default=0.05, help="delay per successful 25-post AppView batch")
    p.add_argument("--max-gap-hours", type=float, default=12.0)
    p.add_argument("--min-age-days", type=float, default=7.0,
                   help="both posts must be at least this old when outcomes are finalized")
    p.add_argument("--min-substantive-tokens", type=int, default=3)
    p.add_argument("--max-core-jaccard", type=float, default=0.70)
    p.add_argument("--max-core-sequence", type=float, default=0.85)
    p.add_argument("--title-similarity", type=float, default=0.20,
                   help="reject shared URL when two nonempty external-card titles are very different")
    p.add_argument("--winner-min-reposts", type=int, default=8)
    p.add_argument("--winner-min-margin", type=int, default=5)
    p.add_argument("--winner-min-ratio", type=float, default=2.0)
    p.add_argument("--include-adult", action="store_true")
    p.add_argument("--seed", type=int, default=20260907,
                   help="deterministic X/Y randomization seed")
    return p.parse_args()


def parse_dt(s: str) -> datetime:
    s = str(s or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def post_uri(did: str, rkey: str) -> str:
    return f"at://{did}/{POST_COLLECTION}/{rkey}"


def chunks(xs: list[str], n: int) -> Iterable[list[str]]:
    for i in range(0, len(xs), n):
        yield xs[i:i+n]


def canonicalize_url(raw: str) -> str | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        u = urllib.parse.urlsplit(raw if "://" in raw else "https://" + raw)
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
        clean_q = []
        for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True):
            lk = k.lower()
            if lk.startswith("utm_") or lk in TRACKING_KEYS:
                continue
            if host.endswith("amazon.com") and lk == "tag":
                continue
            clean_q.append((k, v))
        clean_q.sort()
        out = host + path
        if clean_q:
            out += "?" + urllib.parse.urlencode(clean_q, doseq=True)
        return out
    except (ValueError, UnicodeError):
        return None


def host_path(canon: str) -> tuple[str, str]:
    u = urllib.parse.urlsplit("https://" + canon)
    return (u.hostname or "").lower(), u.path or "/"


def looks_adult(*parts: str) -> bool:
    return bool(ADULT_RE.search(" ".join(str(x or "") for x in parts)))


def raw_url_hard_reject(canon: str) -> bool:
    """High-precision *offline* rejects only. Avoid aggressive rules pre-hydration."""
    c = canonicalize_url(canon)
    if not c:
        return True
    host, path = host_path(c)
    low = path.lower()
    seg = [s for s in low.split("/") if s]
    segset = set(seg)
    if path in ("", "/"):
        return True
    if host in SOCIAL_OR_GENERIC_DOMAINS:
        return True
    if host in ADULT_DOMAINS or looks_adult(host, path):
        return True
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?", host) or host == "localhost":
        return True
    if segset & GENERIC_PATH_TOKENS:
        return True
    if host in GENERIC_SERVICE_RULES:
        if host == "twitch.tv" and len(seg) == 1:
            return True
        if host in {"ko-fi.com", "paypal.me", "line.me", "patreon.com"} and len(seg) <= 2:
            return True
    if host.endswith("amazon.com") and low in {"/s", "/gp/search"}:
        return True
    if host.endswith("open.spotify.com") and seg and seg[0] in {"playlist", "artist", "user"}:
        return True
    if (host, low) in {("allquakes.com", "/app/report-a-quake.php"), ("artblast.co", "/subscribe")}:
        return True
    if "nerve-center" in low:
        return True
    return False


def substantive_token_count(text: str) -> int:
    clean = URLISH_RE.sub(" ", str(text or ""))
    return len(re.findall(r"(?u)\b[^\W_]{2,}\b", clean))


def core_tokens(text: str) -> list[str]:
    clean = URLISH_RE.sub(" ", str(text or ""))
    clean = MENTION_TAG_RE.sub(" ", clean)
    return re.findall(r"(?u)\b[^\W_]+\b", clean.lower())


def core_similarity(a: str, b: str) -> tuple[float, float]:
    aa, bb = core_tokens(a), core_tokens(b)
    sa, sb = set(aa), set(bb)
    jac = len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0
    seq = difflib.SequenceMatcher(None, " ".join(aa), " ".join(bb)).ratio()
    return jac, seq


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k); fields.append(k)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        if not fields:
            return
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def prefilter(inp: Path, out: Path, cfg: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    state = json.loads((inp / "state.json").read_text(encoding="utf-8"))
    ws = parse_dt(state["window_start"])
    we = parse_dt(state["window_end"])
    raw: list[dict[str, str]] = []
    with (inp / "candidate_pairs.csv").open(newline="", encoding="utf-8-sig") as f:
        raw = list(csv.DictReader(f))

    reasons = Counter()
    kept_rows: list[dict[str, Any]] = []
    for r in raw:
        rs: list[str] = []
        try:
            at, bt = parse_dt(r["a_created_at"]), parse_dt(r["b_created_at"])
            gap = abs((bt - at).total_seconds()) / 3600.0
        except Exception:
            rs.append("timestamp_parse_error"); gap = 999
            at = bt = ws
        if not (ws <= at <= we and ws <= bt <= we):
            rs.append("created_at_outside_requested_window")
        if gap > cfg.max_gap_hours:
            rs.append("gap_over_max")
        if substantive_token_count(r.get("a_text", "")) < cfg.min_substantive_tokens:
            rs.append("a_too_little_text")
        if substantive_token_count(r.get("b_text", "")) < cfg.min_substantive_tokens:
            rs.append("b_too_little_text")
        jac, seq = core_similarity(r.get("a_text", ""), r.get("b_text", ""))
        if jac >= cfg.max_core_jaccard or seq >= cfg.max_core_sequence:
            rs.append("near_duplicate_copy")
        if raw_url_hard_reject(r.get("canonical_url", "")):
            rs.append("obviously_unusable_raw_target")
        if looks_adult(r.get("a_text", ""), r.get("b_text", ""), r.get("canonical_url", "")) and not cfg.include_adult:
            rs.append("adult_or_porn")
        if rs:
            reasons.update(rs)
            continue
        x = dict(r)
        x["offline_core_jaccard"] = round(jac, 4)
        x["offline_core_sequence"] = round(seq, 4)
        x["offline_gap_hours"] = round(gap, 4)
        kept_rows.append(x)

    # One row per actual post pair. If several raw URLs generated the same pair,
    # hydration will determine the real shared structured URL.
    by_pair: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in kept_rows:
        ar, br = sorted((r["a_rkey"], r["b_rkey"]))
        key = (r["did"], ar, br)
        # Prefer a longer/more specific raw target as representative metadata.
        old = by_pair.get(key)
        if old is None or len(str(r.get("canonical_url", ""))) > len(str(old.get("canonical_url", ""))):
            by_pair[key] = r
    pairs = list(by_pair.values())
    pairs.sort(key=lambda r: (r["did"], r["a_created_at"], r["b_created_at"], r["a_rkey"], r["b_rkey"]))
    uris = sorted({post_uri(r["did"], rk) for r in pairs for rk in (r["a_rkey"], r["b_rkey"])})
    write_csv(out / "prefilter_pairs.csv", pairs)
    (out / "hydration_uris.txt").write_text("\n".join(uris) + ("\n" if uris else ""), encoding="utf-8")
    summary = {
        "raw_candidate_rows": len(raw),
        "offline_kept_rows_before_pair_dedup": len(kept_rows),
        "offline_unique_post_pairs": len(pairs),
        "offline_unique_posts_to_hydrate": len(uris),
        "appview_batches_of_25": math.ceil(len(uris) / 25),
        "authors_after_prefilter": len({r["did"] for r in pairs}),
        "rejection_counts": dict(reasons),
    }
    (out / "prefilter_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return pairs, summary


def api_json(endpoint: str, params: list[tuple[str, str]], sleep_s: float, retries: int = 8) -> dict[str, Any]:
    url = f"{APPVIEW}/{endpoint}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "bsky-retweet-validation/1.0"})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.load(resp)
            if sleep_s:
                time.sleep(sleep_s)
            return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = e.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(2 ** attempt, 30)
            print(f"AppView HTTP {e.code}; retry in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)
        except Exception as e:
            last = e
            delay = min(2 ** attempt, 30)
            print(f"AppView error {e!r}; retry in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)
    raise RuntimeError(f"AppView failed after {retries} attempts: {last}")


def load_cache(path: Path) -> dict[str, dict[str, Any] | None]:
    out: dict[str, dict[str, Any] | None] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            out[obj["uri"]] = obj.get("post")
    return out


def append_cache(path: Path, entries: list[tuple[str, dict[str, Any] | None]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for uri, post in entries:
            f.write(json.dumps({"uri": uri, "post": post}, ensure_ascii=False) + "\n")


def hydrate(uris: list[str], cache_path: Path, cfg: argparse.Namespace) -> dict[str, dict[str, Any] | None]:
    cache = load_cache(cache_path)
    missing = [u for u in uris if u not in cache]
    print(f"Hydration: {len(uris)} unique posts; cached={len(uris)-len(missing)}; missing={len(missing)}")
    if cfg.offline and missing:
        raise SystemExit(f"--offline requested but {len(missing)} posts are not cached")
    for i, batch in enumerate(chunks(missing, 25), 1):
        data = api_json("app.bsky.feed.getPosts", [("uris", u) for u in batch], cfg.sleep)
        returned = {p.get("uri"): p for p in data.get("posts", []) if p.get("uri")}
        entries = []
        for u in batch:
            post = returned.get(u)
            cache[u] = post
            entries.append((u, post))
        append_cache(cache_path, entries)
        if i % 20 == 0 or i == math.ceil(len(missing)/25):
            print(f"  fetched {min(i*25, len(missing))}/{len(missing)}")
    return cache


def extract_sources(post: dict[str, Any]) -> tuple[set[str], set[str]]:
    facets: set[str] = set(); embeds: set[str] = set()
    record = post.get("record") or {}
    for facet in record.get("facets") or []:
        for feat in (facet or {}).get("features") or []:
            if (feat or {}).get("$type") == "app.bsky.richtext.facet#link":
                c = canonicalize_url(feat.get("uri", ""))
                if c: facets.add(c)
    for ent in record.get("entities") or []:
        if (ent or {}).get("type") == "link":
            c = canonicalize_url(ent.get("value", ""))
            if c: facets.add(c)
    def add(e: Any) -> None:
        if not isinstance(e, dict): return
        typ = e.get("$type", "")
        if typ in ("app.bsky.embed.external", "app.bsky.embed.external#view") or "external" in e:
            ext = e.get("external")
            if isinstance(ext, dict):
                c = canonicalize_url(ext.get("uri", ""))
                if c: embeds.add(c)
        if typ in ("app.bsky.embed.recordWithMedia", "app.bsky.embed.recordWithMedia#view") or "media" in e:
            add(e.get("media"))
    add(record.get("embed")); add(post.get("embed"))
    return facets, embeds


def external_cards(post: dict[str, Any]) -> dict[str, dict[str, str]]:
    cards: dict[str, dict[str, str]] = {}
    def add(e: Any) -> None:
        if not isinstance(e, dict): return
        typ = e.get("$type", "")
        if typ in ("app.bsky.embed.external", "app.bsky.embed.external#view") or "external" in e:
            ext = e.get("external")
            if isinstance(ext, dict):
                c = canonicalize_url(ext.get("uri", ""))
                if c:
                    cards[c] = {"title": str(ext.get("title") or ""), "description": str(ext.get("description") or "")}
        if typ in ("app.bsky.embed.recordWithMedia", "app.bsky.embed.recordWithMedia#view") or "media" in e:
            add(e.get("media"))
    rec = post.get("record") or {}; add(rec.get("embed")); add(post.get("embed"))
    return cards


def word_jaccard(a: str, b: str) -> float:
    aa = set(re.findall(r"(?u)\b\w+\b", str(a).lower())); bb = set(re.findall(r"(?u)\b\w+\b", str(b).lower()))
    return len(aa & bb)/len(aa | bb) if aa | bb else 1.0


def target_flags(canon: str, ca: dict[str, str] | None, cb: dict[str, str] | None, cfg: argparse.Namespace) -> list[str]:
    host, path = host_path(canon); low = path.lower(); seg = [s for s in low.split("/") if s]; segset = set(seg)
    flags: list[str] = []
    if path in ("", "/"): flags.append("root")
    if host in SOCIAL_OR_GENERIC_DOMAINS: flags.append("social")
    if host in ADULT_DOMAINS or looks_adult(host, path): flags.append("adult")
    if segset & GENERIC_PATH_TOKENS: flags.append("generic_path")
    if segset & DYNAMIC_PATH_TOKENS: flags.append("dynamic_path")
    if "radio" in host or "weather" in host: flags.append("dynamic_domain")
    if host == "twitch.tv" and len(seg) == 1: flags.append("generic_service")
    if host in {"ko-fi.com", "paypal.me", "line.me", "patreon.com"} and len(seg) <= 2: flags.append("generic_service")
    if host.endswith("amazon.com") and low in {"/s", "/gp/search"}: flags.append("generic_listing")
    if host.endswith("open.spotify.com") and seg and seg[0] in {"playlist", "artist", "user"}: flags.append("mutable_collection")
    if (host, low) in {("allquakes.com", "/app/report-a-quake.php"), ("artblast.co", "/subscribe")}: flags.append("dynamic_landing")
    if "nerve-center" in low: flags.append("dynamic_landing")
    if low.endswith("/live") and host not in {"youtube.com", "youtu.be"}: flags.append("dynamic_landing")
    if ca and cb:
        ta, tb = ca.get("title", "").strip(), cb.get("title", "").strip()
        if ta and tb and ta != tb and word_jaccard(ta, tb) < cfg.title_similarity:
            flags.append("title_mismatch")
    return flags


def stable_target(flags: list[str]) -> bool:
    bad = {"root", "social", "adult", "generic_path", "dynamic_path", "dynamic_domain", "generic_service", "generic_listing", "mutable_collection", "dynamic_landing", "title_mismatch"}
    return not bool(bad & set(flags))


def decisive(ar: int, br: int, cfg: argparse.Namespace) -> bool:
    hi, lo = max(ar, br), min(ar, br)
    if hi < cfg.winner_min_reposts: return False
    if hi - lo < cfg.winner_min_margin: return False
    ratio = math.inf if lo == 0 else hi / lo
    return ratio >= cfg.winner_min_ratio


def finalize(inp: Path, out: Path, pairs: list[dict[str, Any]], posts: dict[str, dict[str, Any] | None], cfg: argparse.Namespace) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    reason_counts = Counter(); accepted: list[dict[str, Any]] = []; rejected: list[dict[str, Any]] = []
    for r in pairs:
        did = r["did"]; au = post_uri(did, r["a_rkey"]); bu = post_uri(did, r["b_rkey"])
        a, b = posts.get(au), posts.get(bu); reasons: list[str] = []
        if not a: reasons.append("a_missing_or_deleted")
        if not b: reasons.append("b_missing_or_deleted")
        if reasons:
            x=dict(r); x["reasons"]=";".join(reasons); rejected.append(x); reason_counts.update(reasons); continue
        adid=((a.get("author") or {}).get("did") or ""); bdid=((b.get("author") or {}).get("did") or "")
        if adid != did or bdid != did: reasons.append("author_mismatch")
        arec=a.get("record") or {}; brec=b.get("record") or {}
        atext=str(arec.get("text") or ""); btext=str(brec.get("text") or "")
        try:
            at=parse_dt(arec.get("createdAt") or r["a_created_at"]); bt=parse_dt(brec.get("createdAt") or r["b_created_at"])
            gap=abs((bt-at).total_seconds())/3600.0
        except Exception:
            at=parse_dt(r["a_created_at"]); bt=parse_dt(r["b_created_at"]); gap=abs((bt-at).total_seconds())/3600.0
            reasons.append("live_timestamp_parse_fallback")
        if gap > cfg.max_gap_hours: reasons.append("gap_over_max")
        if now - at < timedelta(days=cfg.min_age_days) or now - bt < timedelta(days=cfg.min_age_days): reasons.append("outcome_not_mature")
        if substantive_token_count(atext) < cfg.min_substantive_tokens: reasons.append("a_too_little_text")
        if substantive_token_count(btext) < cfg.min_substantive_tokens: reasons.append("b_too_little_text")
        jac, seq=core_similarity(atext,btext)
        if jac >= cfg.max_core_jaccard or seq >= cfg.max_core_sequence: reasons.append("near_duplicate_copy")
        fa,ea=extract_sources(a); fb,eb=extract_sources(b)
        pa=fa if fa else ea; pb=fb if fb else eb; shared=pa & pb
        if not shared:
            reasons.append("no_shared_primary_structured_url")
            x=dict(r); x.update({"a_primary_urls":" | ".join(sorted(pa)),"b_primary_urls":" | ".join(sorted(pb)),"reasons":";".join(reasons)})
            rejected.append(x); reason_counts.update(reasons); continue
        ca,cb=external_cards(a),external_cards(b)
        candidates=[]
        for u in sorted(shared):
            fl=target_flags(u,ca.get(u),cb.get(u),cfg)
            candidates.append((0 if stable_target(fl) else 1, len(fl), -len(u), u, fl))
        candidates.sort(); _,_,_,target,flags=candidates[0]
        if not stable_target(flags): reasons.append("no_specific_stable_target")
        handle=(a.get("author") or {}).get("handle") or (b.get("author") or {}).get("handle") or did
        display=(a.get("author") or {}).get("displayName") or (b.get("author") or {}).get("displayName") or ""
        if looks_adult(handle,display,target,atext,btext,(ca.get(target) or {}).get("title",""),(cb.get(target) or {}).get("title","")) and not cfg.include_adult:
            reasons.append("adult_or_porn")
        ar=int(a.get("repostCount") or 0); br=int(b.get("repostCount") or 0)
        hi=max(ar,br); lo=min(ar,br); ratio=(math.inf if lo==0 and hi>0 else (hi/lo if lo else 1.0))
        x=dict(r)
        x.update({
            "handle":handle,"selected_target":target,"target_flags":";".join(flags),
            "a_uri":au,"b_uri":bu,"a_text_live":atext,"b_text_live":btext,
            "a_created_at_live":at.isoformat(),"b_created_at_live":bt.isoformat(),"gap_hours_live":round(gap,4),
            "core_jaccard_live":round(jac,4),"core_sequence_live":round(seq,4),
            "a_reposts":ar,"b_reposts":br,"winner_reposts":hi,"loser_reposts":lo,
            "repost_margin":hi-lo,"winner_loser_ratio":("inf" if math.isinf(ratio) else round(ratio,4)),
            "repost_winner":"A" if ar>br else "B" if br>ar else "TIE",
            "decisive_outcome":int(decisive(ar,br,cfg)),
            "a_likes":int(a.get("likeCount") or 0),"b_likes":int(b.get("likeCount") or 0),
            "a_quotes":int(a.get("quoteCount") or 0),"b_quotes":int(b.get("quoteCount") or 0),
            "reasons":";".join(reasons),
        })
        if reasons:
            rejected.append(x); reason_counts.update(reasons)
        else:
            accepted.append(x)

    decisive_rows=[r for r in accepted if int(r["decisive_outcome"])==1]
    decisive_rows.sort(key=lambda r:(-int(r["winner_reposts"]),-int(r["repost_margin"]),r["handle"],r["a_rkey"]))

    # Deterministic, approximately perfectly balanced X/Y answer labels.
    # The model-facing file contains NO outcome, account, URL, timestamp or engagement metadata.
    rng = random.Random(cfg.seed)
    shuffled = decisive_rows.copy()
    rng.shuffle(shuffled)
    start_x = bool(rng.getrandbits(1))
    questions: list[dict[str, Any]] = []
    answer_key: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for i, r in enumerate(shuffled):
        pair_id = hashlib.sha256(f"{r['did']}|{r['a_rkey']}|{r['b_rkey']}".encode()).hexdigest()[:16]
        desired_correct = "X" if ((i % 2 == 0) == start_x) else "Y"
        # Orient the winning original post into desired_correct.
        a_wins = r["repost_winner"] == "A"
        if desired_correct == "X":
            x_is_a = a_wins
        else:
            x_is_a = not a_wins
        x_text = r["a_text_live"] if x_is_a else r["b_text_live"]
        y_text = r["b_text_live"] if x_is_a else r["a_text_live"]
        questions.append({"pair_id": pair_id, "X": x_text, "Y": y_text})
        key = {
            "pair_id": pair_id, "correct_answer": desired_correct,
            "winner_reposts": r["winner_reposts"], "loser_reposts": r["loser_reposts"],
            "repost_margin": r["repost_margin"], "winner_loser_ratio": r["winner_loser_ratio"],
            "publication_gap_hours": r["gap_hours_live"], "handle": r["handle"],
            "target": r["selected_target"], "source_a_uri": r["a_uri"], "source_b_uri": r["b_uri"],
        }
        answer_key.append(key)
        audit_rows.append({**questions[-1], **key})

    write_csv(out/"validated_pairs.csv",accepted)
    write_csv(out/"rejected_after_hydration.csv",rejected)
    write_csv(out/"decisive_pairs.csv",decisive_rows)
    write_csv(out/"llm_questions.csv",questions)
    write_csv(out/"llm_answer_key.csv",answer_key)
    write_csv(out/"llm_pairs_audit.csv",audit_rows)
    summary={
        "offline_unique_pairs_entering_hydration":len(pairs),
        "structurally_valid_semantically_filtered_pairs":len(accepted),
        "decisive_llm_pairs":len(decisive_rows),
        "decisive_accounts":len({r['did'] for r in decisive_rows}),
        "decisive_thresholds":{
            "winner_min_reposts":cfg.winner_min_reposts,
            "winner_min_margin":cfg.winner_min_margin,
            "winner_min_ratio":cfg.winner_min_ratio,
            "max_pair_gap_hours":cfg.max_gap_hours,
            "min_post_age_days":cfg.min_age_days,
        },
        "rejection_counts_after_hydration":dict(reason_counts),
        "llm_label_balance": dict(Counter(r["correct_answer"] for r in answer_key)),
        "top_decisive_pairs":[{
            "handle":r["handle"],"reposts":f"{r['a_reposts']} vs {r['b_reposts']}",
            "margin":r["repost_margin"],"target":r["selected_target"]
        } for r in decisive_rows[:20]],
    }
    (out/"final_summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    return summary


def main() -> int:
    cfg=args(); inp=Path(cfg.input_dir); out=Path(cfg.out) if cfg.out else inp/"llm-final"; out.mkdir(parents=True,exist_ok=True)
    pairs, pre=prefilter(inp,out,cfg)
    print(json.dumps(pre,indent=2))
    if cfg.prefilter_only:
        print(f"\nPrefilter complete: {out}"); return 0
    uris=sorted({post_uri(r["did"],rk) for r in pairs for rk in (r["a_rkey"],r["b_rkey"])})
    cache=Path(cfg.cache) if cfg.cache else out/"appview_posts.jsonl"
    posts=hydrate(uris,cache,cfg)
    summary=finalize(inp,out,pairs,posts,cfg)
    print("\n"+json.dumps(summary,indent=2,ensure_ascii=False))
    print(f"\nModel-facing file: {out/'llm_questions.csv'}")
    print(f"Answer key:        {out/'llm_answer_key.csv'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
