#!/usr/bin/env python3
"""Produce data/site_data.json for the public site.

Every number the site shows comes from here, and this file is computed by
the same analysis code that produces the published report -- the site can
never drift from the report because they read the same source.

P46/P44 discipline encoded here:
  - descriptive distributions are published as-is
  - cross-vertical comparisons are exposure-matched and reported with the
    Holm-Bonferroni corrected p-value, never as a "significant" claim
  - the three denominators (products / repos / domains) are never mixed
"""
import argparse
import collections
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from paths import ROOT, DATA
except ImportError:
    ROOT = os.path.dirname(HERE)
    DATA = os.path.join(ROOT, "data")

from analyze import (build, load, cohort_table, vertical_year_table,
                     vertical_significance, MIN_AGE_DAYS, MIN_N)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(DATA, "ledger.jsonl"))
    ap.add_argument("--repo", default=os.path.join(DATA, "repo_status.jsonl"))
    ap.add_argument("--domain", default=os.path.join(DATA, "liveness.jsonl"))
    ap.add_argument("--categories", default=os.path.join(DATA, "categories.jsonl"))
    ap.add_argument("--out", default=os.path.join(DATA, "site_data.json"))
    args = ap.parse_args()

    ledger = load(args.ledger)
    repo_status = load(args.repo)
    domain_status = load(args.domain)
    categories = load(args.categories)

    products = build(ledger, repo_status, domain_status)

    # Attach the vertical: the category follows the product's earliest
    # Show HN post. Same join as analyze.py / render_report.py.
    cat_by_id = {c.get("id"): c.get("category")
                 for c in categories if c.get("id")}
    cat_by_key = {}
    for r in ledger:
        key = r.get("repo") or r.get("base") or r.get("domain")
        cid = cat_by_id.get(r.get("id"))
        if key and cid:
            cat_by_key.setdefault(key, cid)
    for p in products:
        p["category"] = cat_by_key.get(p.get("key"))

    high = [p for p in products if p.get("confidence") == "high"]
    medium = [p for p in products if p.get("confidence") == "medium"]

    out = {}
    out["generated"] = "2026-11-12"
    out["min_age_days"] = MIN_AGE_DAYS

    # --- three separate denominators, never interchangeable ---
    out["posts"] = len(ledger)
    out["products"] = len(products)
    out["repos_total"] = len({r.get("repo") for r in repo_status if r.get("repo")})
    out["repos_checked"] = len({r.get("repo") for r in repo_status
                                if r.get("repo") and r.get("verdict")})
    out["domains_total"] = len({d.get("domain") for d in domain_status
                                if d.get("domain")})
    out["domains_checked"] = len({d.get("domain") for d in domain_status
                                  if d.get("domain") and d.get("verdict")})
    out["high_n"] = len(high)
    out["med_n"] = len(medium)

    out["high_verdicts"] = dict(collections.Counter(p["verdict"] for p in high))
    out["med_verdicts"] = dict(collections.Counter(p["verdict"] for p in medium))

    out["cohorts_high"] = [
        {k: r[k] for k in ("year", "n", "dead", "dormant", "slowing",
                           "archived", "active", "dead_pct",
                           "not_maintained_pct", "publishable")}
        for r in cohort_table(high)
    ]

    # --- death timing (descriptive, no significance test needed) ---
    # survival_days == verification date - launch day == how long we watched
    dead = sorted(p["exposure_days"] for p in high
                  if p.get("verdict") == "dead" and p.get("exposure_days"))

    def q(pct):
        if not dead:
            return 0
        i = pct * (len(dead) - 1)
        lo = int(i)
        hi = min(lo + 1, len(dead) - 1)
        return int(round(dead[lo] + (dead[hi] - dead[lo]) * (i - lo)))

    out["death_n"] = len(dead)
    out["death_q"] = {"P25": q(.25), "P50": q(.50),
                      "P75": q(.75), "P90": q(.90)}

    buckets = []
    prev = 0
    for label, hi_v in [("90 天内", 90), ("90–180 天", 180),
                         ("180–365 天", 365), ("365–547 天", 547),
                         ("547–730 天", 730), ("730 天以上", 10 ** 9)]:
        cnt = sum(1 for d in dead if prev <= d < hi_v)
        buckets.append({"label": label, "n": cnt,
                        "pct": round(cnt * 100 / len(dead), 1) if dead else 0})
        prev = hi_v
    out["death_buckets"] = buckets
    out["within_1y"] = round(sum(1 for d in dead if d < 365) * 100 / len(dead), 1) if dead else 0
    out["within_2y"] = round(sum(1 for d in dead if d < 730) * 100 / len(dead), 1) if dead else 0

    # --- exposure curve ---
    exposure = []
    prev = 0
    for label, hi_v in [("180 天以内", 180), ("180–365 天", 365),
                        ("365–547 天", 547), ("547–730 天", 730),
                        ("730 天以上", 10 ** 9)]:
        grp = [p for p in high
               if p.get("exposure_days") is not None
               and prev <= p["exposure_days"] < hi_v]
        n = len(grp)
        dcount = sum(1 for p in grp if p["verdict"] == "dead")
        exposure.append({"label": label, "n": n, "dead": dcount,
                         "pct": round(dcount * 100 / n, 1) if n else 0})
        prev = hi_v
    out["exposure"] = exposure

    # --- verticals, per launch year, with the significance test ---
    out["verticals_2024"] = [
        {k: r[k] for k in ("category", "verified", "dead",
                           "dead_pct_verified", "not_maintained_pct_verified",
                           "publishable")}
        for r in sorted(vertical_year_table(products, tier="high", year="2024"),
                        key=lambda x: -x["dead_pct_verified"])
    ]
    out["sig_2024"] = vertical_significance(products, tier="high", year="2024",
                                            min_age_days=MIN_AGE_DAYS)

    # --- HN points strata, exposure-matched -------------------------------
    # points is measured on launch day, so it is the one forward-looking
    # signal in the dataset. Fixed exposure first, then exact one-sided
    # binomial, then Holm-Bonferroni over the tests actually performed.
    def _pmf(i, n, p0):
        if p0 <= 0:
            return 1.0 if i == 0 else 0.0
        if p0 >= 1:
            return 1.0 if i == n else 0.0
        return math.exp(math.lgamma(n + 1) - math.lgamma(i + 1)
                        - math.lgamma(n - i + 1)
                        + i * math.log(p0) + (n - i) * math.log(1 - p0))

    def _sf(k, n, p0):
        return sum(_pmf(i, n, p0) for i in range(k, n + 1))

    def _cdf(k, n, p0):
        return sum(_pmf(i, n, p0) for i in range(0, k + 1))

    strata = []
    for year in ("2024", "2025"):
        grp = [p for p in high
               if p.get("launch_day", "").startswith(year)
               and p.get("exposure_days") is not None
               and p["exposure_days"] >= MIN_AGE_DAYS]
        if len(grp) < MIN_N:
            continue
        hi = [p for p in grp if (p.get("points") or 0) >= 20]
        lo = [p for p in grp if (p.get("points") or 0) < 20]
        dh = sum(1 for p in hi if p["verdict"] == "dead")
        dl = sum(1 for p in lo if p["verdict"] == "dead")
        if not hi or not lo:
            continue
        plo, phi = dl / len(lo), dh / len(hi)
        strata.append({
            "year": year,
            "n": len(grp),
            "base_dead": sum(1 for p in grp if p["verdict"] == "dead"),
            "base_pct": round(sum(1 for p in grp if p["verdict"] == "dead")
                              * 100 / len(grp), 2),
            "lo_n": len(lo), "lo_dead": dl, "lo_pct": round(plo * 100, 2),
            "hi_n": len(hi), "hi_dead": dh, "hi_pct": round(phi * 100, 2),
            "p_hi_lower": _cdf(dh, len(hi), plo),
            "p_lo_higher": _sf(dl, len(lo), phi),
        })

    # Holm-Bonferroni over the tests actually performed (P: multiple
    # comparisons must be corrected over the real family, not a nominal one)
    family = [(s["year"], k, s[k]) for s in strata
              for k in ("p_hi_lower", "p_lo_higher")]
    family.sort(key=lambda x: x[2])
    m = len(family)
    adj_map = {}
    for i, (y, k, pv) in enumerate(family):
        adj_map[(y, k)] = min(1.0, pv * (m - i))
    for s in strata:
        s["p_hi_lower_adj"] = adj_map[(s["year"], "p_hi_lower")]
        s["p_lo_higher_adj"] = adj_map[(s["year"], "p_lo_higher")]
        s["significant"] = bool(s["p_hi_lower_adj"] < 0.05
                                and s["p_lo_higher_adj"] < 0.05)
    out["points_strata"] = strata
    out["points_threshold"] = 20

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"wrote {args.out}")
    print(f"  posts={out['posts']} products={out['products']} "
          f"repos={out['repos_checked']}/{out['repos_total']} "
          f"domains={out['domains_checked']}/{out['domains_total']}")
    print(f"  death n={out['death_n']} within1y={out['within_1y']}% "
          f"within2y={out['within_2y']}%")
    print(f"  verticals tested={len(out['sig_2024'])} "
          f"significant={sum(1 for s in out['sig_2024'] if s['significant'])}")


if __name__ == "__main__":
    main()
