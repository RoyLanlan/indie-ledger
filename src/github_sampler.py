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
from github_enrich import (api, rate_limit, classify_repo, TOKEN,
                           RateLimitError, UnavailableError,
                           SecondaryRateLimit, BlockedRepo)


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
    ap.add_argument("--delay", type=float, default=0.35,
                    help="seconds between repo lookups; raise this if the "
                         "secondary rate limit fires")
    ap.add_argument("--cooldown", type=int, default=300,
                    help="seconds to back off after a secondary rate limit")
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
            try:
                info = api(f"/repos/{repo}")
            except RateLimitError:
                # A rate-limited lookup is NOT evidence the repo was deleted.
                # Put it back and stop the batch without recording a verdict.
                todo.insert(0, repo)
                print(f"   rate limited on {repo}; no verdict recorded, "
                      f"backing off", flush=True)
                out.flush()
                time.sleep(120)
                break
            except UnavailableError:
                # 451: repo exists but has been made unavailable (legal takedown).
                # That is neither "deleted" nor "alive" - record it as its own
                # verdict so it is excluded from death rates.
                ev = {"verdict": "unavailable",
                      "reason": "HTTP 451 (repo blocked for legal reasons)",
                      "survival_days": None}
                rec = {"repo": repo, "launch_day": repo_launch[repo], **ev,
                       "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                vc[ev["verdict"]] += 1
                checked += 1
                continue
            except BlockedRepo:
                # 403 "Repository access blocked" - a per-repo ToS/DMCA block.
                # The repo still exists. It is not dead and it is not alive.
                # Verdict is `repo_blocked`, NOT `blocked`: in the domain tier
                # `blocked` means "site answers 403/405/429", which is a sign
                # the site is ALIVE. Collapsing the two names would quietly
                # drop live sites out of the denominator.
                ev = {"verdict": "repo_blocked",
                      "reason": "HTTP 403 (repo access blocked, ToS/DMCA)",
                      "survival_days": None}
                rec = {"repo": repo, "launch_day": repo_launch[repo], **ev,
                       "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                vc[ev["verdict"]] += 1
                checked += 1
                continue
            except SecondaryRateLimit:
                # Abuse-detection throttle. It does NOT consume core quota, so
                # sleeping until the quota resets would be wrong. Back off hard
                # and slow the request cadence permanently for this run.
                todo.insert(0, repo)
                print(f"   secondary rate limit on {repo}; no verdict recorded, "
                      f"backing off {args.cooldown}s", flush=True)
                out.flush()
                delay = args.cooldown
                time.sleep(delay)
                break
            ev = classify_repo(info, repo_launch[repo])
            rec = {"repo": repo, "launch_day": repo_launch[repo], **ev,
                   "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            vc[ev["verdict"]] += 1
            checked += 1
            time.sleep(args.delay + random.uniform(0, 0.1))
        out.flush()
        print(f"  batch done: checked={checked} {dict(vc)} rate={rate_limit()}", flush=True)

    out.close()
    print("DONE", dict(vc), "total checked:", checked)


if __name__ == "__main__":
    main()
