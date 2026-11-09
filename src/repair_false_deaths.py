#!/usr/bin/env python3
"""
P13 repair: re-verify every row whose verdict was recorded because the API
call failed rather than because the repo was actually gone.

Background: `api()` used to return None both for a genuine 404 and for an
exhausted rate-limit retry loop. `classify_repo(None)` mapped that to
"dead / repo 404 (deleted)". Measured on 2026-11-09: 3 of 25 rows marked
"repo 404" were actually alive repos -> 12% false death rate.

This script finds every row whose evidence is a failure-derived verdict and
re-checks it against the live API. Rows that turn out to be alive are
corrected in place; rows that are genuinely gone stay.

Usage:
  python3 repair_false_deaths.py            # dry run, prints a plan
  python3 repair_false_deaths.py --apply    # write corrections
"""
import json, os, sys, argparse, datetime, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from github_enrich import api, RateLimitError, UnavailableError, classify_repo

LEDGER = os.path.join(HERE, "data", "ledger.jsonl")
STATUS = os.path.join(HERE, "data", "repo_status.jsonl")

# verdicts that can only be produced by a failed lookup
FAILURE_VERDICTS = {"dead"}          # + reason contains "404"
FAILURE_REASON_MARKERS = ("404",)


def load(path):
    out = []
    with open(path) as f:
        for l in f:
            try:
                out.append(json.loads(l))
            except Exception:
                pass
    return out


def suspect(rec):
    """Is this row's death verdict potentially a failed lookup?"""
    if rec.get("verdict") != "dead":
        return False
    reason = (rec.get("reason") or "").lower()
    if "archived" in reason:
        return False          # archived is a real, unambiguous signal
    return any(m in reason for m in FAILURE_REASON_MARKERS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default=STATUS)
    args = ap.parse_args()

    ledger = load(LEDGER)
    launch = {}
    for r in ledger:
        if r.get("repo"):
            launch.setdefault(r["repo"], (r.get("day") or "")[:10])

    rows = load(STATUS)
    sus = [r for r in rows if suspect(r)]
    print(f"total rows: {len(rows)}")
    print(f"rows marked dead via 404 (suspect): {len(sus)}")

    if not sus:
        print("nothing to repair")
        return 0

    fixed, confirmed_gone, blocked, skipped = [], [], [], []
    for i, rec in enumerate(sus):
        repo = rec["repo"]
        try:
            info = api(f"/repos/{repo}")
        except RateLimitError:
            skipped.append(repo)
            print(f"  rate limited on {repo}; leaving row untouched")
            continue
        except UnavailableError:
            # 451: repo exists but is blocked for legal reasons.
            # It was never deleted - record it as `unavailable`.
            blocked.append(repo)
            continue
        if info is None:
            confirmed_gone.append(repo)
        else:
            # the repo is alive -> the old "dead" verdict was wrong
            ev = classify_repo(info, launch.get(repo))
            fixed.append((repo, rec, ev, info))
        if (i + 1) % 25 == 0:
            print(f"  rechecked {i+1}/{len(sus)}  fixed={len(fixed)} "
                  f"gone={len(confirmed_gone)}", flush=True)

    print(f"\nrechecked {len(sus)}")
    print(f"  confirmed genuinely deleted: {len(confirmed_gone)}")
    print(f"  wrongly marked dead (alive): {len(fixed)}")
    print(f"  blocked for legal reasons (451): {len(blocked)}")
    print(f"  skipped (rate limited): {len(skipped)}")

    if fixed:
        print("\nexamples of wrongly-marked-dead repos:")
        for repo, old, ev, info in fixed[:15]:
            print(f"  {repo:<42} was={old.get('reason')!r} "
                  f"now={ev.get('verdict')} ({ev.get('reason')})")

    if not args.apply:
        print("\nDRY RUN. re-run with --apply to write corrections.")
        return 0

    if not fixed and not blocked:
        print("\nnothing to correct.")
        return 0

    # rewrite the status file with corrections
    by_repo = {r["repo"]: r for r in rows}
    for repo, old, ev, info in fixed:
        r = by_repo[repo]
        r.update(ev)
        r["corrected_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds")
        r["correction"] = "P13: original verdict was a failed lookup, not a 404"
    for repo in blocked:
        r = by_repo[repo]
        r["verdict"] = "unavailable"
        r["reason"] = "HTTP 451 (repo blocked for legal reasons)"
        r["corrected_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds")
        r["correction"] = "P13: 451 is not a 404"

    backup = STATUS + ".pre-p13.bak"
    shutil.copy2(STATUS, backup)
    with open(STATUS, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nwrote {STATUS} ({len(rows)} rows); backup at {backup}")
    print(f"corrected {len(fixed)} alive-as-dead, {len(blocked)} 451-as-dead")
    return 0


if __name__ == "__main__":
    sys.exit(main())
