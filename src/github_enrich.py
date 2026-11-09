#!/usr/bin/env python3
"""
Stage 2: GitHub-side enrichment -> per-repo liveness + survival timeline.

For each repo found in the ledger:
  - repo 404        -> dead (deleted)
  - archived=true   -> dead (explicit)
  - pushed_at age   -> dormant thresholds
  - created_at      -> project start date (survival baseline)

Needs a GitHub PAT for meaningful throughput:
  unauth core = 60/hr  -> 5,000 repos = ~83 hours  (unusable)
  PAT   core = 5000/hr -> 5,000 repos = ~1 hour
PAT is read from $GITHUB_TOKEN or ~/.hermes/mcp-tokens/github-* ; values are
never printed or written to the output files.
"""
import json, os, sys, time, urllib.request, urllib.parse, urllib.error, datetime
from collections import Counter

UA = {'User-Agent': 'Mozilla/5.0 (compatible; indie-ledger/0.1; +research)',
      'Accept': 'application/vnd.github+json'}
HERE = os.path.dirname(os.path.abspath(__file__))


def load_token():
    """Return a GitHub token from env or token files. Never logged."""
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    tdir = os.path.expanduser("~/.hermes/mcp-tokens")
    if os.path.isdir(tdir):
        for fn in sorted(os.listdir(tdir)):
            if not fn.startswith("github"):
                continue
            try:
                raw = open(os.path.join(tdir, fn)).read()
            except Exception:
                continue
            import re
            m = re.search(r'(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})', raw)
            if m:
                return m.group(1)
    return None


TOKEN = load_token()


def api(path, timeout=30, retries=3):
    h = dict(UA)
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    url = path if path.startswith("http") else "https://api.github.com" + path
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=h)
            r = urllib.request.urlopen(req, timeout=timeout)
            return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (403, 429):
                # rate limited — back off hard
                wait = 60 if e.code == 429 else 30
                print(f"   rate limited ({e.code}); sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if attempt == retries - 1:
                return None
            time.sleep(2 * (attempt + 1))
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def rate_limit():
    d = api("/rate_limit")
    if not d:
        return None
    r = d.get("resources", {})
    return {"core": f"{r.get('core',{}).get('remaining')}/{r.get('core',{}).get('limit')}",
            "search": f"{r.get('search',{}).get('remaining')}/{r.get('search',{}).get('limit')}",
            "authed": bool(TOKEN)}


def classify_repo(info, launch_day):
    """
    Map repo metadata + launch date to a survival verdict.
    launch_day: the Show HN post date, i.e. when the project publicly launched.
    """
    ev = {}
    # survival window since public launch — needed for EVERY verdict, including dead
    try:
        ev["survival_days"] = (datetime.date.today() -
                               datetime.date.fromisoformat(launch_day)).days
    except Exception:
        ev["survival_days"] = None

    if info is None:
        ev["verdict"] = "dead"
        ev["reason"] = "repo 404 (deleted)"
        return ev
    ev.update({
        "archived": info.get("archived"),
        "pushed_at": (info.get("pushed_at") or "")[:10],
        "created_at": (info.get("created_at") or "")[:10],
        "stars": info.get("stargazers_count"),
        "open_issues": info.get("open_issues_count"),
    })
    if info.get("archived"):
        ev["verdict"] = "dead"
        ev["reason"] = "archived"
        return ev
    try:
        pushed = datetime.date.fromisoformat(ev["pushed_at"])
        age = (datetime.date.today() - pushed).days
        ev["age_days"] = age
        if age > 365:
            ev["verdict"] = "dormant"
        elif age > 180:
            ev["verdict"] = "slowing"
        else:
            ev["verdict"] = "active"
        ev["reason"] = f"last push {age}d ago"
    except Exception:
        ev["verdict"] = "unknown"
        ev["reason"] = "no pushed_at"
    return ev


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(HERE, "data", "ledger.jsonl"))
    ap.add_argument("--out", default=os.path.join(HERE, "data", "repo_status.jsonl"))
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--delay", type=float, default=0.25)
    args = ap.parse_args()

    print("auth:", "PAT present" if TOKEN else "NO TOKEN (unauth limits: 60/hr core)")
    print("rate:", rate_limit())

    rows = [json.loads(l) for l in open(args.ledger)]
    repo_launch = {}
    for r in rows:
        if r.get("repo"):
            repo_launch.setdefault(r["repo"], (r.get("day") or "")[:10])
    repos = sorted(repo_launch.keys())
    if args.limit:
        repos = repos[:args.limit]
    print(f"repos to check: {len(repos)}")

    done = set()
    if os.path.exists(args.out):
        for l in open(args.out):
            try:
                done.add(json.loads(l)["repo"])
            except Exception:
                pass
        print(f"already done: {len(done)}")

    out = open(args.out, "a")
    vc = Counter()
    for i, repo in enumerate(repos):
        if repo in done:
            continue
        info = api(f"/repos/{repo}")
        ev = classify_repo(info, repo_launch[repo])
        rec = {"repo": repo, "launch_day": repo_launch[repo], **ev,
               "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        vc[ev["verdict"]] += 1
        if (i + 1) % 50 == 0:
            print(f"   {i+1}/{len(repos)}  {dict(vc)}", flush=True)
        time.sleep(args.delay)
    out.close()
    print("DONE", dict(vc))
    print("final rate:", rate_limit())


if __name__ == "__main__":
    main()
