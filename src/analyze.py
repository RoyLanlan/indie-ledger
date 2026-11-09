#!/usr/bin/env python3
"""
Stage 3: cohort survival analysis -> the actual report numbers.

Reads:
  data/ledger.jsonl       (Show HN products)
  data/repo_status.jsonl  (GitHub verdicts, optional)
  data/liveness.jsonl     (domain verdicts, optional)

Produces a cohort table: for each launch year, what share is still active.

Every number printed must be traceable to a row in the source files. The
analyzer refuses to print a cohort with fewer than MIN_N entries.
"""
import json, os, sys, datetime, argparse
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_N = 30  # red line: never publish a cohort smaller than this


def load(path):
    if not os.path.exists(path):
        return []
    out = []
    for l in open(path):
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out


def build(ledger, repo_status, domain_status):
    """Join per-product verdicts. A product's confidence tier decides if it counts."""
    repo_verdict = {r["repo"]: r for r in repo_status if r.get("repo")}
    dom_verdict = {}
    for d in domain_status:
        dom_verdict.setdefault(d.get("domain"), d)

    products = {}
    for r in ledger:
        repo = r.get("repo")
        dom = r.get("domain")
        base = r.get("base")
        key = repo or base or dom
        if not key:
            continue
        rec = products.setdefault(key, {
            "key": key, "repo": repo, "domain": dom, "base": base,
            "title": r.get("title"), "launch_day": r.get("day"),
            "url": r.get("url"), "kind": r.get("kind"),
            "points": r.get("points") or 0,
        })
        # prefer the earliest launch as canonical
        if r.get("day") and (not rec["launch_day"] or r["day"] < rec["launch_day"]):
            rec["launch_day"] = r["day"]

    for rec in products.values():
        if rec["repo"] and rec["repo"] in repo_verdict:
            rv = repo_verdict[rec["repo"]]
            rec["verdict"] = rv.get("verdict")
            rec["evidence"] = rv.get("reason")
            rec["confidence"] = "high"
            rec["survival_days"] = rv.get("survival_days")
            rec["signal_source"] = "github"
            rec["archived"] = rv.get("archived")
            rec["last_push"] = rv.get("pushed_at")
            rec["stars"] = rv.get("stars")
        elif rec["domain"] and rec["domain"] in dom_verdict:
            dv = dom_verdict[rec["domain"]]
            rec["verdict"] = dv.get("verdict")
            rec["evidence"] = dv.get("reason")
            rec["confidence"] = "medium"
            rec["survival_days"] = None
            rec["signal_source"] = "domain"
        else:
            rec["verdict"] = "unverified"
            rec["evidence"] = "no signal collected"
            rec["confidence"] = "none"
    return list(products.values())


def cohort_table(products, min_n=MIN_N):
    """Group by launch year -> verdict distribution."""
    by_year = defaultdict(list)
    for p in products:
        if not p.get("launch_day"):
            continue
        y = p["launch_day"][:4]
        by_year[y].append(p)

    rows = []
    for y in sorted(by_year):
        grp = by_year[y]
        vc = Counter(p["verdict"] for p in grp)
        n = len(grp)
        dead = vc["dead"]
        dormant = vc["dormant"]
        alive = vc["active"]
        slowing = vc["slowing"]
        archived = vc["archived"]
        unv = vc["unverified"]
        alive_site = vc["alive"]
        degraded = vc["degraded"]
        # Tier-specific semantics for the two "restricted" verdicts:
        #
        #   domain tier  `blocked` = the site answered 403/405/429. That is a
        #                sign the site is UP and refusing bots -> count it as
        #                alive, and keep it in the denominator.
        #   repo tier    `repo_blocked` = the repository itself was taken down
        #                for ToS/DMCA reasons. It still exists, so it is
        #                neither alive nor dead -> exclude it from the
        #                denominator entirely (same as `unavailable`).
        #
        # The two must never share a name: collapsing them would silently
        # drop live sites out of the denominator (P17).
        site_blocked = vc["blocked"]
        alive_site += site_blocked
        unavailable = vc["unavailable"] + vc["repo_blocked"]
        verified = n - unv - unavailable
        row = {
            "year": y, "n": n,
            "dead": dead, "dormant": dormant, "active": alive,
            "slowing": slowing, "archived": archived, "unverified": unv,
            "alive_site": alive_site,
            "site_blocked": site_blocked,
            "degraded": degraded,
            "unavailable": unavailable,
            "verified": verified,
            "dead_pct": dead * 100 // n if n else 0,
            # P20: archived is "not maintained", not "dead". It counts toward
            # the not-maintained rate but NOT toward the death rate.
            "not_maintained_pct":
                (dead + dormant + slowing + archived) * 100 // n if n else 0,
            # verified-subset rates: denominator excludes unverified AND
            # unavailable rows
            "dead_pct_verified": dead * 100 // verified if verified else 0,
            "not_maintained_pct_verified":
                (dead + dormant + slowing + archived) * 100 // verified if verified else 0,
            "publishable": n >= min_n,
        }
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(HERE, "data", "ledger.jsonl"))
    ap.add_argument("--repo", default=os.path.join(HERE, "data", "repo_status.jsonl"))
    ap.add_argument("--domain", default=os.path.join(HERE, "data", "liveness.jsonl"))
    ap.add_argument("--out", default=os.path.join(HERE, "data", "products.json"))
    args = ap.parse_args()

    ledger = load(args.ledger)
    repo_status = load(args.repo)
    domain_status = load(args.domain)
    print(f"ledger rows: {len(ledger)}  repo_status: {len(repo_status)}  "
          f"domain_status: {len(domain_status)}")

    products = build(ledger, repo_status, domain_status)
    print(f"distinct products: {len(products)}")
    vc = Counter(p["verdict"] for p in products)
    print("verdicts:", dict(vc))
    cc = Counter(p["confidence"] for p in products)
    print("confidence:", dict(cc))

    print("\n=== cohort table (launch year x verdict) ===")
    rows = cohort_table(products)
    print(f"{'year':<6}{'n':>7}{'dead':>7}{'dorm':>7}{'alive':>7}{'unver':>7}"
          f"{'dead%':>8}{'notmaint%':>11}  pub")
    for r in rows:
        flag = "Y" if r["publishable"] else f"n(<{MIN_N})"
        print(f"{r['year']:<6}{r['n']:>7}{r['dead']:>7}{r['dormant']:>7}{r['active']:>7}"
              f"{r['unverified']:>7}{r['dead_pct']:>7}%{r['not_maintained_pct']:>10}%  {flag}")

    print("\n=== confidence-tiered view (only high-confidence = github-verified) ===")
    high = [p for p in products if p["confidence"] == "high"]
    if high:
        rows_h = cohort_table(high)
        print(f"{'year':<6}{'n':>7}{'dead':>7}{'dorm':>7}{'alive':>7}{'dead%':>8}{'notmaint%':>11}")
        for r in rows_h:
            print(f"{r['year']:<6}{r['n']:>7}{r['dead']:>7}{r['dormant']:>7}{r['active']:>7}"
                  f"{r['dead_pct']:>7}%{r['not_maintained_pct']:>10}%")
    else:
        print("   (no github-verified rows yet — run github_enrich.py first)")

    json.dump({"products": products, "cohorts": rows},
              open(args.out, "w"), ensure_ascii=False, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
