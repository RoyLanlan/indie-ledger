#!/usr/bin/env python3
"""
Stage 2c: quota-aware GitHub sampler.

Works with OR without a PAT:
  - With PAT    : 5000 core req/hr -> can do the whole 24,767 repo set in ~5h
  - Without PAT : 60 core req/hr    -> ~60 repos/hour, accumulates forever

The sampler is resumable and append-only, so it can run unattended for days
and be upgraded to full speed the moment a PAT appears.

Priority: repos from EARLIER cohorts first. Those are the ones with enough
elapsed time to have died, so they carry the survival signal. New repos are
almost always still active and teach us nothing about mortality.

Run:  python3 github_sampler.py [--pat-from-file PATH]
"""
import json, os, sys, time, argparse, datetime, random
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from github_enrich import api, rate_limit, classify_repo, TOKEN


def load_ledger(path):
    rows = []
    with open(path) as f:
        for l in f:
            try:
                rows.append(json.loads(l))
            except Exception:
                pass
    return rows


def load_token_from_file(path):
    """Read a token from a file. The value is never printed or logged."""
    try:
        raw = open(os.path.expanduser(path)).read().strip()
    except Exception:
        return None
    import re
    m = re.search(r'(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})', raw)
    return m.group(1) if m else (raw if len(raw) > 20 else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(HERE, "data", "ledger.jsonl"))
    ap.add_argument("--out", default=os.path.join(HERE, "data", "repo_status.jsonl"))
    ap.add_argument("--pat-file", default="")
    ap.add_argument("--batch", type=int, default=0, help="0 = run until quota exhausted")
    ap.add_argument("--idle-sleep", type=int, default=60)
    args = ap.parse_args()

    global TOKEN
    if args.pat_file:
        t = load_token_from_file(args.pat_file)
        if t:
            TOKEN = t
            print("PAT loaded from file")
    print("auth:", "PAT" if TOKEN else "unauth (60/hr)")

    rows = load_ledger(args.ledger)
    repo_launch = {}
    for r in rows:
        if r.get("repo"):
            repo_launch.setdefault(r["repo"], (r.get("day") or "")[:10])
    print(f"repos in ledger: {len(repo_launch)}")

    done = set()
    if os.path.exists(args.out):
        for l in open(args.out):
            try:
                done.add(json.loads(l)["repo"])
            except Exception:
                pass
    print(f"already done: {len(done)}")

    # priority: oldest launch year first (most elapsed time -> most informative)
    todo = [r for r in repo_launch if r not in done]
    todo.sort(key=lambda r: (repo_launch[r], r))
    print(f"todo: {len(todo)} (oldest cohorts first)")

    out = open(args.out, "a")
    vc = Counter()
    checked = 0
    limit = args.batch or 10**9

    while checked < limit and todo:
        rl = rate_limit()
        if not rl:
            print("could not read rate limit; sleeping", file=sys.stderr)
            time.sleep(args.idle_sleep)
            continue
        rem = int(rl["core"].split("/")[0]) if "/" in rl["core"] else 0
        cap = int(rl["core"].split("/")[1]) if "/" in rl["core"] else 60
        if rem <= 2:
            print(f"quota exhausted ({rl['core']}); sleeping {args.idle_sleep}s", flush=True)
            time.sleep(args.idle_sleep)
            continue

        # take a batch sized to remaining quota
        batch = min(rem - 1, limit - checked, len(todo))
        for i in range(batch):
            repo = todo.pop(0)
            info = api(f"/repos/{repo}")
            ev = classify_repo(info, repo_launch[repo])
            rec = {"repo": repo, "launch_day": repo_launch[repo], **ev,
                   "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            vc[ev["verdict"]] += 1
            checked += 1
            time.sleep(0.2 + random.uniform(0, 0.15))
        out.flush()
        print(f"  batch done: checked={checked} {dict(vc)} rate={rate_limit()}", flush=True)

    out.close()
    print("DONE", dict(vc), "total checked:", checked)


if __name__ == "__main__":
    main()
