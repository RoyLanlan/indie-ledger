#!/usr/bin/env python3
"""
Audit the `dead` verdict against the live GitHub API.

Why this exists (P20): four separate bugs (P13, P16, P17, P20) all had the
same shape - something that was still alive got recorded as dead. The
archived-vs-dead bug (P20) survived every unit test because the test
asserted the buggy behaviour. Only an end-to-end re-check against the real
API caught it: 100 of 207 "dead" rows were archived repos.

This script re-verifies a random sample of `dead` rows by actually asking
GitHub. A death verdict is only valid if the API returns 404.

Usage:
  python3 audit_deaths.py                 # sample 25, print a report
  python3 audit_deaths.py --sample 100
  python3 audit_deaths.py --apply         # relabel rows that are not really dead
  python3 audit_deaths.py --threshold 5   # fail if the false-death rate is above this %

Exit code 1 if the false-death rate exceeds --threshold (for cron use).
"""
import json, os, sys, time, random, argparse, datetime
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.expanduser("~/.hermes/mcp-tokens/github-token")
UA = {"User-Agent": "indie-ledger-audit"}


def load_token():
    try:
        with open(TOKEN_FILE) as f:
            t = f.read().strip()
            return t or None
    except OSError:
        return None


def check(repo, token):
    """Return (verdict_from_api, detail)."""
    h = dict(UA)
    if token:
        h["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}"
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(url, headers=h), timeout=25)
        d = json.loads(r.read())
        return "exists", {
            "archived": d.get("archived"),
            "pushed_at": (d.get("pushed_at") or "")[:10],
            "stars": d.get("stargazers_count"),
        }
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "404", {}
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:120]
        except Exception:
            pass
        return f"http{e.code}", {"body": body}
    except Exception as e:
        return type(e).__name__, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-status",
                    default=os.path.join(HERE, "data", "repo_status.jsonl"))
    ap.add_argument("--sample", type=int, default=25)
    ap.add_argument("--threshold", type=float, default=5.0,
                    help="max acceptable false-death rate, percent")
    ap.add_argument("--max-inconclusive", type=float, default=20.0,
                    help="max acceptable share of inconclusive lookups, percent")
    ap.add_argument("--apply", action="store_true",
                    help="relabel rows that are not really dead")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    token = load_token()
    if not token:
        print("no PAT found - an unauthenticated audit may be rate limited")
    else:
        print(f"auth: PAT")

    rows = []
    with open(args.repo_status) as f:
        for l in f:
            l = l.strip()
            if not l:
                continue
            try:
                r = json.loads(l)
            except Exception:
                continue
            if r.get("verdict") == "dead":
                rows.append(r)
    print(f"dead rows in ledger: {len(rows)}")
    if not rows:
        print("nothing to audit")
        return 0

    random.seed(args.seed)
    sample = random.sample(rows, min(args.sample, len(rows)))
    print(f"auditing {len(sample)} rows against the live API\n")

    real_dead = 0
    false_dead = []
    inconclusive = []
    for i, r in enumerate(sample, 1):
        repo = r["repo"]
        verdict, detail = check(repo, token)
        if verdict == "404":
            real_dead += 1
            status = "OK  (really gone)"
        elif verdict == "exists":
            if detail.get("archived"):
                status = f"WRONG -> archived (alive, pushed {detail.get('pushed_at')})"
            else:
                status = f"WRONG -> alive (pushed {detail.get('pushed_at')})"
            false_dead.append((repo, detail))
        else:
            status = f"inconclusive ({verdict})"
            inconclusive.append((repo, detail))
        print(f"  [{i:>3}/{len(sample)}] {repo:<45} {status}")
        time.sleep(0.4)

    n = len(sample)
    false_rate = len(false_dead) / n * 100 if n else 0
    inconclusive_rate = len(inconclusive) / n * 100 if n else 0
    print()
    print(f"confirmed dead (404) : {real_dead}")
    print(f"NOT dead             : {len(false_dead)}  ({false_rate:.1f}%)")
    print(f"inconclusive         : {len(inconclusive)}  ({inconclusive_rate:.1f}%)")

    if false_dead:
        print("\nfalse deaths:")
        for repo, d in false_dead:
            print(f"  {repo:<45} archived={d.get('archived')} "
                  f"pushed={d.get('pushed_at')} stars={d.get('stars')}")

    if args.apply and false_dead:
        fixed = 0
        with open(args.repo_status) as f:
            all_rows = [json.loads(l) for l in f if l.strip()]
        targets = {r for r, _ in false_dead}
        with open(args.repo_status, "w") as f:
            for r in all_rows:
                if r.get("repo") in targets and r.get("verdict") == "dead":
                    d = dict(r)
                    if d.get("archived"):
                        d["verdict"] = "archived"
                        d["reason"] = "archived (read-only, still exists)"
                    else:
                        d["verdict"] = "uncertain"
                        d["reason"] = "audit: repo exists, not a 404"
                    d["corrected_by"] = "audit_deaths.py"
                    d["corrected_at"] = datetime.datetime.now(
                        datetime.timezone.utc).isoformat(timespec="seconds")
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")
                    fixed += 1
                else:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nrelabelled {fixed} rows")

    # An audit with no signal must NOT pass. If most lookups were
    # inconclusive (bad token, rate limit, network), the false-death rate
    # measured over the few conclusive rows is meaningless. P21: never
    # report a PASS that was computed from a mostly-inconclusive sample.
    if inconclusive_rate > args.max_inconclusive:
        print(f"\nFAIL: {inconclusive_rate:.1f}% of lookups were inconclusive "
              f"(limit {args.max_inconclusive:.0f}%) - the audit has no signal, "
              f"so its {false_rate:.1f}% false-death rate proves nothing")
        return 1

    if false_rate > args.threshold:
        print(f"\nFAIL: false-death rate {false_rate:.1f}% exceeds "
              f"threshold {args.threshold:.1f}%")
        return 1
    print(f"\nPASS: false-death rate {false_rate:.1f}% "
          f"(threshold {args.threshold:.1f}%), {inconclusive_rate:.1f}% inconclusive")
    return 0


if __name__ == "__main__":
    sys.exit(main())
