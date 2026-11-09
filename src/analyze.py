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
import json, os, sys, datetime, argparse, math
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_N = 30  # red line: never publish a cohort smaller than this


def _binom_tail(n, k, p):
    """Exact one-sided binomial tail P(X >= k | n, p), without overflow.

    math.comb(n, k) is an exact int, but converting it to float overflows
    once n reaches the thousands - the naive sum then raises
    OverflowError and the whole report stage dies. This uses the
    log-gamma form instead:

        log C(n,k) = lgamma(n+1) - lgamma(k+1) - lgamma(n-k+1)

    and exponentiates each term. Same value, no overflow. The terms are
    summed from k upward; for a small tail the largest term is the
    first one, so the sum is well conditioned.
    """
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    log_p = math.log(p)
    log_q = math.log(1.0 - p)
    total = 0.0
    for i in range(k, n + 1):
        log_c = (math.lgamma(n + 1) - math.lgamma(i + 1)
                 - math.lgamma(n - i + 1))
        total += math.exp(log_c + i * log_p + (n - i) * log_q)
    return min(total, 1.0)


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


def vertical_table(products, min_n=MIN_N, tier=None):
    """Group by industry vertical -> verdict distribution.

    This is the reason the whole ledger exists: "which verticals die and
    which survive". Same red line as the cohort table -- a vertical with
    fewer than MIN_N verified rows is never published, because a rate
    computed on 6 rows is noise dressed as a finding.

    `verified` is the denominator: rows with no signal (unverified) and
    rows whose state cannot be determined (unavailable / repo_blocked)
    are excluded, for the same reason they are excluded from the cohort
    rates.

    `tier` selects one confidence tier. The default (None) mixes tiers,
    which the project forbids (P17): a GitHub verdict and a domain verdict
    are different kinds of evidence and must never share a denominator.
    Callers pass "high" or "medium" explicitly.
    """
    by_cat = defaultdict(list)
    for p in products:
        if tier and p.get("confidence") != tier:
            continue
        cat = p.get("category")
        if not cat:
            continue
        by_cat[cat].append(p)

    rows = []
    for cat in sorted(by_cat):
        grp = by_cat[cat]
        vc = Counter(p["verdict"] for p in grp)
        n = len(grp)
        dead = vc["dead"]
        dormant = vc["dormant"]
        alive = vc["active"]
        slowing = vc["slowing"]
        archived = vc["archived"]
        unv = vc["unverified"]
        alive_site = vc["alive"] + vc["blocked"]
        degraded = vc["degraded"]
        unavailable = vc["unavailable"] + vc["repo_blocked"]
        verified = n - unv - unavailable
        rows.append({
            "category": cat,
            "n": n,
            "verified": verified,
            "dead": dead, "dormant": dormant, "active": alive,
            "slowing": slowing, "archived": archived,
            "alive_site": alive_site, "degraded": degraded,
            "unverified": unv, "unavailable": unavailable,
            "dead_pct_verified": dead * 100 / verified if verified else 0,
            "not_maintained_pct_verified":
                (dead + dormant + slowing + archived) * 100 / verified
                if verified else 0,
            "active_pct_verified": alive * 100 / verified if verified else 0,
            "publishable": verified >= min_n,
        })
    return rows


def vertical_year_table(products, min_n=MIN_N, tier="high", year=None):
    """Vertical x launch year -> verified death rate.

    This is the clean version of the vertical comparison. Pooling launch
    years into one rate is confounded: a product launched in 2024 has had
    ~2.8 years of exposure to die, one launched in 2026 has had months.
    Comparing verticals only within a single launch year removes that
    confound.
    """
    rows = []
    for p in products:
        if tier and p.get("confidence") != tier:
            continue
        if not p.get("category") or not p.get("launch_day"):
            continue
        if year and p["launch_day"][:4] != year:
            continue
        rows.append(p)
    by_cat = defaultdict(list)
    for p in rows:
        by_cat[p["category"]].append(p)

    out = []
    for cat in sorted(by_cat):
        grp = by_cat[cat]
        vc = Counter(p["verdict"] for p in grp)
        n = len(grp)
        unavailable = vc["unavailable"] + vc["repo_blocked"]
        verified = n - vc["unverified"] - unavailable
        dead = vc["dead"]
        notmaint = dead + vc["dormant"] + vc["slowing"] + vc["archived"]
        out.append({
            "category": cat, "n": n, "verified": verified,
            "dead": dead,
            "dead_pct_verified": dead * 100 / verified if verified else 0,
            "not_maintained_pct_verified":
                notmaint * 100 / verified if verified else 0,
            "publishable": verified >= min_n,
        })
    return out


def vertical_significance(products, tier="high", year=None, min_n=MIN_N):
    """Is a vertical's death rate actually different from the baseline?

    A vertical with 3 dead out of 40 looks dramatic and may be noise.
    This computes an exact one-sided binomial tail under the null that
    the vertical dies at the same rate as its own cohort. Only verticals
    where the rate is significantly higher than baseline are worth
    calling out; everything else is ordinary variation.

    Returns a list of dicts sorted by p-value.
    """
    rows = [p for p in products
            if (not tier or p.get("confidence") == tier)
            and p.get("category") and p.get("launch_day")
            and (not year or p["launch_day"][:4] == year)]
    if not rows:
        return []

    def verified_of(group):
        vc = Counter(p["verdict"] for p in group)
        n = len(group)
        unavailable = vc["unavailable"] + vc["repo_blocked"]
        return n - vc["unverified"] - unavailable, vc["dead"]

    base_ver, base_dead = verified_of(rows)
    if base_ver <= 0 or base_dead >= base_ver:
        return []
    base_p = base_dead / base_ver

    by_cat = defaultdict(list)
    for p in rows:
        by_cat[p["category"]].append(p)

    out = []
    for cat, grp in by_cat.items():
        ver, dead = verified_of(grp)
        if ver < min_n or dead == 0:
            continue
        p_hat = dead / ver
        # Exact one-sided binomial tail: P(X >= dead | n=ver, p=base_p).
        # math.comb() returns an exact int that overflows float
        # conversion once n is in the thousands, so the log-gamma form
        # is used instead: log C(n,k) + k*log(p) + (n-k)*log(1-p),
        # exponentiated term by term. Same number, no overflow.
        tail = _binom_tail(ver, dead, base_p)
        out.append({
            "category": cat,
            "verified": ver,
            "dead": dead,
            "rate": p_hat,
            "baseline": base_p,
            "ratio": p_hat / base_p if base_p else 0,
            "p_value": tail,
            "significant": tail < 0.05,
        })
    out.sort(key=lambda r: r["p_value"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(HERE, "data", "ledger.jsonl"))
    ap.add_argument("--repo", default=os.path.join(HERE, "data", "repo_status.jsonl"))
    ap.add_argument("--domain", default=os.path.join(HERE, "data", "liveness.jsonl"))
    ap.add_argument("--categories", default=os.path.join(HERE, "data", "categories.jsonl"))
    ap.add_argument("--out", default=os.path.join(HERE, "data", "products.json"))
    args = ap.parse_args()

    ledger = load(args.ledger)
    repo_status = load(args.repo)
    domain_status = load(args.domain)
    categories = load(args.categories)
    print(f"ledger rows: {len(ledger)}  repo_status: {len(repo_status)}  "
          f"domain_status: {len(domain_status)}  categories: {len(categories)}")

    products = build(ledger, repo_status, domain_status)

    # Attach the vertical. A product may have several Show HN posts; the
    # canonical title is the earliest launch, and the category follows
    # that title. Every post of the same product gets a category, so the
    # join is by post id -> product key.
    cat_by_id = {c.get("id"): c for c in categories if c.get("id")}
    cat_by_key = {}
    for r in ledger:
        key = r.get("repo") or r.get("base") or r.get("domain")
        cid = cat_by_id.get(r.get("id"))
        if key and cid and cid.get("category"):
            # First post of the product wins (ledger is chronological).
            cat_by_key.setdefault(key, cid["category"])
    for p in products:
        p["category"] = cat_by_key.get(p.get("key"))

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

    # P17: the two confidence tiers are separate tables with separate
    # denominators. A GitHub verdict and a domain verdict are different
    # kinds of evidence; averaging them together hides both.
    for tier, label in (("high", "HIGH confidence (GitHub-verified)"),
                        ("medium", "MEDIUM confidence (domain-verified)")):
        print(f"\n=== vertical table -- {label} ===")
        vrows = vertical_table(products, tier=tier)
        if not vrows:
            print("   (no rows)")
            continue
        print(f"{'vertical':<24}{'n':>7}{'verif':>7}{'dead':>7}{'dorm':>7}"
              f"{'alive':>7}{'arch':>7}{'dead%':>8}{'notmaint%':>11}  pub")
        for r in sorted(vrows, key=lambda x: -x["n"]):
            flag = "Y" if r["publishable"] else f"n(<{MIN_N})"
            print(f"{r['category']:<24}{r['n']:>7}{r['verified']:>7}"
                  f"{r['dead']:>7}{r['dormant']:>7}{r['active']:>7}"
                  f"{r['archived']:>7}"
                  f"{r['dead_pct_verified']:>7.1f}%"
                  f"{r['not_maintained_pct_verified']:>10.1f}%  {flag}")
        if tier == "high":
            vrows_high = vrows
        else:
            vrows_medium = vrows

    print("\n=== vertical significance vs cohort baseline (exact binomial) ===")
    for yr in sorted({p["launch_day"][:4] for p in products
                       if p.get("launch_day") and p.get("confidence") == "high"}):
        sig = vertical_significance(products, tier="high", year=yr)
        if not sig:
            continue
        print(f"\n  -- {yr} --")
        for r in sig[:8]:
            flag = "SIG" if r["significant"] else "   "
            print(f"   {flag} {r['category']:24} {r['dead']:>4}/{r['verified']:<5} "
                  f"rate={r['rate']*100:5.1f}%  baseline={r['baseline']*100:4.1f}%  "
                  f"x{r['ratio']:4.1f}  p={r['p_value']:.5f}")

    json.dump({"products": products, "cohorts": rows,
               "verticals_high": vrows_high, "verticals_medium": vrows_medium},
              open(args.out, "w"), ensure_ascii=False, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
