#!/usr/bin/env python3
"""
indie-ledger pipeline runner.

Safe to run on a schedule: every stage is resumable, append-only, and
skips work that another process is already doing (lock files).

Usage:
  python3 pipeline.py --stage collect      # L1: backfill missing days
  python3 pipeline.py --stage verify       # L2a+L2b: repo + domain checks
  python3 pipeline.py --stage report       # L3+L4: analyze + render
  python3 pipeline.py --stage all
  python3 pipeline.py --stage status       # progress only, no work
"""
import json, os, sys, subprocess, time, argparse, datetime, fcntl, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
REPO = os.path.join(HERE, "repo")
LOCKS = os.path.join(HERE, ".locks")

os.makedirs(LOCKS, exist_ok=True)
os.makedirs(DATA, exist_ok=True)

TOKEN_FILE = os.path.expanduser("~/.hermes/mcp-tokens/github-token")
HISTORY_START = "2024-01-01"


class Lock:
    """Advisory lock so a cron tick and a manual run never double-work."""

    def __init__(self, name):
        self.path = os.path.join(LOCKS, f"{name}.lock")
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "w")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
            return False
        self.fh.write(f"{os.getpid()} {datetime.datetime.now().isoformat()}\n")
        self.fh.flush()
        return True

    def __exit__(self, *a):
        if self.fh:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
        return False


def linecount(path):
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, "rb") as f:
        for _ in f:
            n += 1
    return n


def today():
    return datetime.date.today().isoformat()


def stage_collect():
    """L1: fetch Show HN posts for days not yet collected."""
    days_dir = os.path.join(DATA, "days")
    os.makedirs(days_dir, exist_ok=True)
    have = {f[:-5] for f in os.listdir(days_dir) if f.endswith(".json")}
    end = today()
    start = HISTORY_START
    missing = []
    d = datetime.date.fromisoformat(start)
    stop = datetime.date.fromisoformat(end)
    while d <= stop:
        k = d.isoformat()
        if k not in have:
            missing.append(k)
        d += datetime.timedelta(days=1)

    if not missing:
        print(f"collect: all days present ({len(have)} days)")
        return 0

    print(f"collect: {len(missing)} missing days, fetching newest first")
    # fetch newest first: recent data is what readers care about
    missing.sort(reverse=True)
    cmd = [sys.executable, os.path.join(HERE, "backfill.py"),
           "--start", missing[-1], "--end", missing[0], "--outdir", DATA]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print("collect FAILED:", r.stderr[-2000:])
        return 1
    print(f"collect: now {len(os.listdir(days_dir))} day files")
    return 0


def stage_verify():
    """L2a: GitHub repo state. L2b: domain liveness."""
    ran = []

    # L2a -- needs PAT
    if os.path.exists(TOKEN_FILE):
        cmd = [sys.executable, os.path.join(HERE, "github_sampler.py"),
               "--idle-sleep", "60",
               # P15: 0.2s bursts trip GitHub's abuse detection, which returns
               # 403 without spending core quota and livelocks the sampler.
               "--delay", "0.35", "--cooldown", "300"]
        print("verify: starting GitHub sampler (bounded to 1 pass)")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5400)
            print(r.stdout[-1500:])
            ran.append(f"github ({r.returncode})")
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            print("verify: github sampler hit its window, stopping cleanly")
            print(out[-1500:])
            ran.append("github (window)")
    else:
        print(f"verify: no PAT at {TOKEN_FILE} -- GitHub stage skipped")
        ran.append("github (no PAT)")

    # L2b -- no auth needed
    cmd = [sys.executable, os.path.join(HERE, "domain_scan.py"), "--delay", "1.0"]
    print("verify: starting domain scan (bounded to 1 pass)")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5400)
        print(r.stdout[-1000:])
        ran.append(f"domains ({r.returncode})")
    except subprocess.TimeoutExpired:
        print("verify: domain scan hit its window, stopping cleanly")
        ran.append("domains (window)")

    print("verify: done ->", ", ".join(ran))
    return 0


def stage_report():
    """L3 + L4: analyze and render."""
    r = subprocess.run([sys.executable, os.path.join(HERE, "analyze.py")],
                       capture_output=True, text=True, timeout=1800)
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print("report: analyze FAILED:", r.stderr[-1000:])
        return 1
    r = subprocess.run([sys.executable, os.path.join(HERE, "render_report.py")],
                       capture_output=True, text=True, timeout=1800)
    print(r.stdout[-1500:])
    if r.returncode != 0:
        print("report: render FAILED:", r.stderr[-1000:])
        return 1

    # publish into the repo working tree
    out = os.path.join(REPO, "data")
    os.makedirs(out, exist_ok=True)
    for name in ("repo_status.jsonl", "liveness.jsonl"):
        src = os.path.join(DATA, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(out, name))
    rep_dir = os.path.join(REPO, "reports")
    os.makedirs(rep_dir, exist_ok=True)
    for f in os.listdir(os.path.join(HERE, "reports")):
        if f.endswith(".md"):
            shutil.copy2(os.path.join(HERE, "reports", f),
                         os.path.join(rep_dir, f))
    print("report: published to repo working tree")
    return 0


def stage_push():
    """Commit and push the refreshed data to GitHub."""
    if not os.path.isdir(os.path.join(REPO, ".git")):
        print("push: no git repo, skipped")
        return 1
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    for cmd in (["git", "add", "-A"],
                ["git", "commit", "-m",
                 "chore: refresh survival ledger data and reports\n\n"
                 "Co-Authored-By: Hermes Agent <noreply@hermes.ai>"],
                ["git", "push", "origin", "master"]):
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                           env=env, timeout=1800)
        tail = (r.stdout + r.stderr).strip().splitlines()
        print(f"  {' '.join(cmd[:2])}: {tail[-1] if tail else '(no output)'}")
        if r.returncode != 0 and cmd[0] == "git" and cmd[1] == "push":
            print("push: push FAILED")
            return 1
    return 0


def stage_status():
    ledger = linecount(os.path.join(DATA, "ledger.jsonl"))
    repos = linecount(os.path.join(DATA, "repo_status.jsonl"))
    doms = linecount(os.path.join(DATA, "liveness.jsonl"))
    days = len(os.listdir(os.path.join(DATA, "days"))) if os.path.exists(
        os.path.join(DATA, "days")) else 0
    print(f"ledger      {ledger:>7,} posts")
    print(f"day files   {days:>7,}")
    print(f"repos       {repos:>7,} / 24,767  ({repos*100//24767}%)")
    print(f"domains     {doms:>7,} / 36,881  ({doms*100//36881}%)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True,
                    choices=["collect", "verify", "report", "push", "all", "status"])
    args = ap.parse_args()

    if args.stage == "status":
        return stage_status()

    stages = {
        "collect": ("collect", stage_collect),
        "verify": ("verify", stage_verify),
        "report": ("report", stage_report),
        "push": ("push", stage_push),
    }
    order = (["collect", "verify", "report", "push"] if args.stage == "all"
             else [args.stage])

    rc = 0
    for s in order:
        name, fn = stages[s]
        with Lock(name) as got:
            if not got:
                print(f"{s}: another process holds the lock, skipping")
                continue
            print(f"=== {s} ===")
            try:
                rc = fn() or rc
            except Exception as e:
                print(f"{s}: EXCEPTION {type(e).__name__}: {e}")
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
