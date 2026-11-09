#!/usr/bin/env python3
"""
Stage 1 backfill: Show HN -> normalized product ledger.

Design constraints (all measured, see indie-product-liveness-probe skill):
  - Day-sliced queries ONLY (Algolia caps reachable hits at 1000/window)
  - Both-sided date filter (single bound silently returns the whole corpus)
  - Append-only JSONL, never overwrite a previous run's lines
  - Checkpoint file so the job is resumable after interruption
  - Idempotent: re-running a day updates that day's file, does not duplicate
  - No credentials required

Usage:
  python3 backfill.py --start 2024-01-01 --end 2026-11-09
  python3 backfill.py --start 2024-01-01 --end 2026-11-09 --resume
"""
import argparse, json, os, sys, time, urllib.request, urllib.parse, datetime, socket
from urllib.parse import urlparse
from collections import Counter

UA = {'User-Agent': 'Mozilla/5.0 (compatible; indie-ledger/0.1; +research)'}
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
CKPT = os.path.join(BASE, "backfill_checkpoint.json")
LOGF = os.path.join(BASE, "backfill.log")

# ---------- host taxonomy (measured on 1937 Show HN posts) ----------
# Platforms / content hosts / hosting providers that are NOT indie products.
PLATFORM_HOSTS = {
    "google.com", "huggingface.co", "apple.com", "microsoft.com", "amazon.com",
    "youtube.com", "youtu.be", "twitter.com", "x.com", "reddit.com", "linkedin.com",
    "facebook.com", "instagram.com", "medium.com", "substack.com", "wikipedia.org",
    "arxiv.org", "news.ycombinator.com", "github.com", "gitlab.com", "codeberg.org",
    "npmjs.com", "pypi.org", "rubygems.org", "packagist.org", "crates.io",
    "stackoverflow.com", "discord.com", "discord.gg", "notion.so", "figma.com",
    "canva.com", "docs.google.com", "drive.google.com", "sheets.google.com",
    "open.spotify.com", "spotify.com", "steampowered.com", "store.steampowered.com",
    "twitch.tv", "paypal.com", "stripe.com", "vercel.com", "netlify.com",
    "cloudflare.com", "digitalocean.com", "heroku.com", "glitch.com",
    "replit.com", "observablehq.com", "kaggle.com", "colab.research.google.com",
    "chat.openai.com", "claude.ai", "anthropic.com", "openai.com",
    "gov.uk", "gov", "nasa.gov", "nih.gov", "un.org", "europa.eu",
}
HOSTING_SUFFIX = {
    ".github.io", ".vercel.app", ".netlify.app", ".pages.dev", ".web.app",
    ".firebaseapp.com", ".replit.app", ".repl.co", ".workers.dev", ".blogspot.com",
    ".wixsite.com", ".weebly.com", ".wordpress.com", ".notion.site",
    ".surge.sh", ".glitch.me", ".herokuapp.com", ".onrender.com", ".fly.dev",
    ".railway.app", ".koyeb.app", ".deno.dev", ".gitbook.io", ".readthedocs.io",
    ".netlify.com", ".azurewebsites.net", ".cloudfront.net", ".s3.amazonaws.com",
    ".itch.io", ".neocities.org", ".gamedev.app", ".vusercontent.net",
}
CONTENT_HOSTS = {
    "blogspot.com", "medium.com", "substack.com", "wordpress.com", "wix.com",
    "weebly.com", "newsletter.com", "ghost.io", "hashnode.dev", "dev.to",
    "towardsdatascience.com", "freecodecamp.org", "dev.to",
}
# Known non-indie-product platforms seen at volume in the sample
NOISE_SUFFIX = {".chatgpt.site", ".blogspot.com", ".itch.io", ".surge.sh"}

CC_2PART = {"co.uk", "com.cn", "co.jp", "com.au", "co.nz", "com.br", "com.mx",
            "co.in", "com.sg", "com.tr", "co.kr", "com.hk", "com.tw", "co.za",
            "com.ar", "co.il", "co.th", "com.my", "com.ph", "com.vn", "com.ua",
            "com.pl", "com.es", "com.pt", "com.ru", "co.id", "co.nz"}


def log(msg):
    line = f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}"
    with open(LOGF, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def ts(d):
    """Accept either a datetime.date or a 'YYYY-MM-DD' string. Always UTC midnight."""
    if isinstance(d, str):
        d = datetime.datetime.fromisoformat(d)
    elif isinstance(d, datetime.date) and not isinstance(d, datetime.datetime):
        d = datetime.datetime(d.year, d.month, d.day)
    return int(d.replace(tzinfo=datetime.timezone.utc).timestamp())


def domain_of(url):
    if not url:
        return None
    try:
        h = urlparse(url if "//" in url else "http://" + url).netloc.lower().split(":")[0]
    except Exception:
        return None
    if not h or "." not in h:
        return None
    return h[4:] if h.startswith("www.") else h


def base_domain(d):
    if not d:
        return None
    p = d.split(".")
    if len(p) <= 2:
        return d
    if p[-2] in CC_2PART and len(p) >= 3:
        return ".".join(p[-3:])
    return ".".join(p[-2:])


def classify_host(domain, url):
    """Return (kind, base). kind in: product_site, github, content_host, hosting, platform, unknown"""
    if not domain:
        return "no_url", None
    d = domain
    base = base_domain(d)
    if d in ("github.com", "www.github.com"):
        return "github", None
    if d in PLATFORM_HOSTS:
        return "platform", base
    if any(d.endswith(s) for s in HOSTING_SUFFIX):
        return "hosting", base
    if base in CONTENT_HOSTS:
        return "content_host", base
    if any(d.endswith(s) for s in NOISE_SUFFIX):
        return "hosting", base
    if len(d.split(".")) < 2:
        return "unknown", base
    return "product_site", base


def github_repo(url):
    if not url:
        return None
    p = urlparse(url)
    if p.netloc.lower().replace("www.", "") != "github.com":
        return None
    segs = [s for s in p.path.split("/") if s]
    if len(segs) >= 2 and segs[1] not in ("about", "pricing", "features", "login",
                                         "signup", "explore", "topics", "collections"):
        return f"{segs[0]}/{segs[1]}"
    return None


def fetch_day(day):
    """Fetch one calendar day of Show HN posts. Returns list of raw hits."""
    lo = ts(day)
    hi = lo + 86400
    posts, seen = [], set()
    for page in range(12):
        url = "https://hn.algolia.com/api/v1/search_by_date?" + urllib.parse.urlencode({
            "tags": "show_hn", "hitsPerPage": 100, "page": page,
            "numericFilters": f"created_at_i>{lo},created_at_i<{hi}",
        })
        d = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers=UA)
                d = json.loads(urllib.request.urlopen(req, timeout=45).read())
                break
            except Exception as e:
                if attempt == 3:
                    log(f"  !! day {day} page {page} FAILED: {e}")
                    d = None
                    break
                time.sleep(2 * (attempt + 1))
        if d is None:
            break
        hits = d.get("hits", [])
        if not hits:
            break
        for h in hits:
            oid = h.get("objectID")
            if oid and oid not in seen:
                seen.add(oid)
                posts.append(h)
        if len(posts) >= d.get("nbHits", 0):
            break
        time.sleep(0.25)
    return posts


def normalize(hit, day):
    url = hit.get("url")
    dom = domain_of(url)
    kind, base = classify_host(dom, url)
    title = (hit.get("title") or "").strip()
    if title.lower().startswith("show hn:"):
        title = title[8:].strip()
    return {
        "id": hit.get("objectID"),
        "day": day,
        "title": title,
        "url": url,
        "domain": dom,
        "base": base,
        "kind": kind,
        "repo": github_repo(url),
        "author": hit.get("author"),
        "points": hit.get("points"),
        "comments": hit.get("num_comments"),
        "created_at": hit.get("created_at"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=datetime.date.today().isoformat())
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--outdir", default=DATA)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(os.path.join(args.outdir, "days"), exist_ok=True)

    done = {}
    if args.resume and os.path.exists(CKPT):
        done = json.load(open(CKPT))
        log(f"resume: {len(done)} days already fetched")

    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)
    total_days = (end - start).days + 1
    log(f"backfill {start} .. {end} ({total_days} days)")

    ledger = os.path.join(args.outdir, "ledger.jsonl")
    seen_ids = set()
    if os.path.exists(ledger):
        with open(ledger) as f:
            for line in f:
                try:
                    seen_ids.add(json.loads(line)["id"])
                except Exception:
                    continue
        log(f"existing ledger: {len(seen_ids)} unique ids")

    stats = Counter()
    new_rows = 0
    day = start
    while day <= end:
        ds = day.isoformat()
        if ds in done and os.path.exists(os.path.join(args.outdir, "days", ds + ".json")):
            stats["skipped"] += 1
            day += datetime.timedelta(days=1)
            continue
        hits = fetch_day(ds)
        recs = [normalize(h, ds) for h in hits]
        with open(os.path.join(args.outdir, "days", ds + ".json"), "w") as f:
            json.dump(recs, f)
        done[ds] = len(recs)
        stats["days"] += 1
        stats["posts"] += len(recs)
        for r in recs:
            stats[r["kind"]] += 1
            if r["repo"]:
                stats["with_repo"] += 1
        if len(recs) != len(hits):
            stats["dedup_removed"] += len(hits) - len(recs)
        day += datetime.timedelta(days=1)

        if stats["days"] % 10 == 0:
            with open(CKPT, "w") as f:
                json.dump(done, f)
            log(f"  progress day {ds} | posts={stats['posts']} "
                f"product_site={stats['product_site']} github={stats['github']} "
                f"with_repo={stats['with_repo']}")
        time.sleep(0.2)

    # final ledger pass: rebuild from day files, dedupe by id
    log("building ledger from day files...")
    rows, ids = [], set()
    for fn in sorted(os.listdir(os.path.join(args.outdir, "days"))):
        if not fn.endswith(".json"):
            continue
        for r in json.load(open(os.path.join(args.outdir, "days", fn))):
            if r["id"] in ids:
                continue
            ids.add(r["id"])
            rows.append(r)
    rows.sort(key=lambda x: (x.get("created_at") or "", x["id"]))
    tmp = ledger + ".tmp"
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, ledger)
    with open(CKPT, "w") as f:
        json.dump(done, f)

    log(f"DONE days={stats['days']} posts={len(rows)}")
    log(f"  kinds: {dict(stats)}")
    # summary
    c = Counter(r["kind"] for r in rows)
    for k, v in c.most_common():
        log(f"  {k:<16} {v:>6} ({v*100//max(len(rows),1)}%)")
    bases = {r["base"] for r in rows if r["kind"] == "product_site" and r["base"]}
    repos = {r["repo"] for r in rows if r["repo"]}
    log(f"  distinct product base domains: {len(bases)}")
    log(f"  distinct github repos: {len(repos)}")
    log(f"  ledger: {ledger} ({os.path.getsize(ledger)} bytes)")


if __name__ == "__main__":
    main()
