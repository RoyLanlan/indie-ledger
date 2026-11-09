#!/usr/bin/env python3
"""
Stage 2b: domain-side liveness scan over the ledger.

No API quota involved (plain DNS + HTTP), so this can cover all 36,881 product
domains. Runs slowly and politely: 1 request/sec, jitter, honest UA.

Writes data/liveness.jsonl (append-only, resumable).
"""
import json, os, sys, time, random, argparse, datetime
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from liveness import classify


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
    ap.add_argument("--ledger", default=os.path.join(HERE, "data", "ledger.jsonl"))
    ap.add_argument("--out", default=os.path.join(HERE, "data", "liveness.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--start-at", type=int, default=0, help="resume offset")
    args = ap.parse_args()

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
    print(f"todo: {len(todo)}")

    out = open(args.out, "a")
    vc = Counter()
    t0 = time.time()
    for i, d in enumerate(todo):
        ev = classify(d)
        rec = {"domain": d, "launch_day": dom_launch[d],
               "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               **ev}
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        vc[ev["verdict"]] += 1
        if (i + 1) % 100 == 0:
            el = time.time() - t0
            rate = (i + 1) / el if el else 0
            left = (len(todo) - i - 1) / rate / 60 if rate else 0
            print(f"   {i+1}/{len(todo)}  {dict(vc)}  {rate:.1f}/s  ETA {left:.0f}min", flush=True)
        time.sleep(args.delay + random.uniform(0, 0.4))
    out.close()
    print("DONE", dict(vc))


if __name__ == "__main__":
    main()
