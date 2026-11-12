#!/usr/bin/env python3
"""
Stage 2b: domain-side liveness scan over the ledger.

No API quota involved (plain DNS + HTTP), so this can cover all 36,881 product
domains. Runs politely: bounded concurrency, jitter, honest UA.

Writes data/liveness.jsonl (append-only, resumable).

Throughput note (2026-11-09): the original serial scan managed only ~11.5
domains/min because each domain costs a `dig` subprocess plus several HTTP
attempts, and DNS timeouts dominate. Measured on a 60-domain sample:
  serial       1.64 s/domain ->  36.6/min
  16 threads   0.30 s/domain -> 196.8/min
Concurrency is the fix. The per-domain verdict rules are unchanged, so the
data stays comparable with everything collected before.
"""
import json, os, sys, time, random, argparse, datetime, threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
from paths import ROOT, DATA as _DATA_DIR, REPORTS as _REPORTS_DIR
sys.path.insert(0, HERE)
from liveness import classify

# Politeness cap: bound the number of in-flight DNS lookups so a burst of
# threads cannot hammer a resolver.
_dns_sem = threading.Semaphore(24)


def load_ledger(path):
    rows = []
    with open(path) as f:
        for l in f:
            try:
                rows.append(json.loads(l))
            except Exception:
                pass
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(ROOT, "data", "ledger.jsonl"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "liveness.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.05,
                    help="pause after the request, not before it")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--start-at", type=int, default=0, help="resume offset")
    ap.add_argument("--lock", default="", help="path to the flock file")
    ap.add_argument("--batch", type=int, default=0,
                    help="stop after N domains (0 = run to completion)")
    args = ap.parse_args()

    # Single-writer lock (P18): the cron-driven scan and a long-running
    # standalone scan must never append to the same file at once.
    if args.lock:
        import fcntl
        os.makedirs(os.path.dirname(args.lock), exist_ok=True)
        lock_fh = open(args.lock, "a+")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"another scanner holds {args.lock}; exiting")
            return 0
        lock_fh.seek(0)
        lock_fh.truncate()
        lock_fh.write(str(os.getpid()))
        lock_fh.flush()
        print(f"lock acquired: {args.lock}")

    rows = load_ledger(args.ledger)
    # collect product domains, excluding noise hosts
    dom_launch = {}
    for r in rows:
        if r.get("kind") != "product_site":
            continue
        d = r.get("domain")
        if not d:
            continue
        dom_launch.setdefault(d, (r.get("day") or "")[:10])
    doms = sorted(dom_launch)
    if args.limit:
        doms = doms[:args.limit]
    print(f"product domains to check: {len(doms)}")

    done = {}
    if os.path.exists(args.out):
        for l in open(args.out):
            try:
                rec = json.loads(l)
                done[rec["domain"]] = rec
            except Exception:
                pass
        print(f"already done: {len(done)}")

    todo = [d for d in doms if d not in done]
    if args.start_at:
        todo = todo[args.start_at:]
    print(f"todo: {len(todo)}  workers: {args.workers}")

    out = open(args.out, "a")
    vc = Counter()
    t0 = time.time()
    n = 0

    def work(d):
        with _dns_sem:
            ev = classify(d)
        if args.delay:
            time.sleep(args.delay + random.uniform(0, 0.2))
        return d, ev

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, d): d for d in todo}
        for fut in as_completed(futs):
            try:
                d, ev = fut.result()
            except Exception:
                continue
            rec = {"domain": d, "launch_day": dom_launch[d],
                   "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                   **ev}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            vc[ev.get("verdict", "unknown")] += 1
            n += 1
            if n % 100 == 0:
                el = time.time() - t0
                rate = n / el if el else 0
                left = (len(todo) - n) / rate / 60 if rate else 0
                print(f"   {n}/{len(todo)}  {dict(vc)}  {rate:.1f}/s  ETA {left:.0f}min",
                      flush=True)
            if args.batch and n >= args.batch:
                for f2 in futs:
                    f2.cancel()
                break

    out.close()
    el = time.time() - t0
    print(f"DONE {dict(vc)}  {n} domains in {el/60:.1f}min "
          f"({n/el if el else 0:.1f}/s)", flush=True)


if __name__ == "__main__":
    main()
