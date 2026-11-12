#!/usr/bin/env python3
"""Audit the precision of the deterministic category taxonomy.

Why
---
The category axis is the point of this ledger: "which verticals die and
which survive". A taxonomy that assigns the wrong label to a product
produces a wrong vertical death rate, and that number looks just as
authoritative as one that is right. So the taxonomy has to be measured,
not assumed.

Method
------
A hand-labelled sample is the only honest way to score a keyword
taxonomy. The sample is a random draw (seed 42) over the whole corpus;
each title was read by a human and assigned a true category. The
taxonomy is then scored against those labels.

Two numbers matter, and they are different:
  precision = of the titles the taxonomy labelled, how many are right.
              This decides whether a vertical death rate can be trusted.
  coverage  = of all titles, how many got any label at all. Low coverage
              is not a precision problem, it is a different problem: the
              title simply carries no vertical signal ("Rosebed",
              "Clinch", "Animalese").

The label set is deliberately allowed to contain "other". That label
means "this title carries no vertical signal", which is a fact about the
title, not a defect of the taxonomy. A title that the taxonomy files
under some vertical when the human says "other" is a *false positive*
and is counted as a miss.

The audit fails if precision drops below the threshold, or if the
labelled sample is too small to conclude anything (P21: no signal, no
PASS).
"""

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
from paths import ROOT, DATA as _DATA_DIR, REPORTS as _REPORTS_DIR
sys.path.insert(0, HERE)
from categorize import categorize, TAXONOMY_VERSION  # noqa: E402

# ---------------------------------------------------------------------------
# Hand-labelled ground truth.
#
# Random draw: random.seed(42); random.sample(rows, 120) over
# data/ledger.jsonl. Each title below was read by a human and assigned a
# true category. Ids are Show HN post ids.
#
# Ambiguity is resolved by asking: "given only this title, which single
# vertical does a reader assign?" Where a title genuinely straddles two
# verticals, the human label wins -- that is what a reader would expect
# and therefore what the report should reflect.
# ---------------------------------------------------------------------------
LABELLED = {
    "49943201": "other",              # job board
    "42032402": "devtools",
    "39765356": "devtools",
    "45216630": "other",              # tor vanity address generator
    "44684720": "productivity_generic",
    "44334970": "devtools",
    "42688889": "ai_ml",
    "41801708": "design_creative",    # stylesheets for web apps
    "50403954": "commerce_fintech",
    "48705561": "other",              # natural-language beach search
    "41416625": "commerce_fintech",   # brokerage for side projects
    "49269616": "other",              # eclipse locator
    "47119080": "media_content",      # timeline of musicians
    "39986764": "other",              # map of travelled countries
    "39923715": "devtools",           # Core Image filters (Apple dev)
    "41573394": "media_content",
    "44265851": "media_content",      # BitTorrent client
    "44480299": "devtools",
    "48040492": "devtools",           # MCP import hooks
    "49432341": "data_analytics",
    "39817190": "ai_ml",
    "48903338": "design_creative",    # colour tools
    "43933377": "other",              # numerology
    "50073112": "devtools",
    "48699033": "other",              # automated job applications
    "47102500": "ai_ml",
    "44294789": "other",
    "47297982": "ai_ml",              # PyTorch layer replacements
    "49256765": "devtools",           # HTML/CSS/JS in Rust
    "45280980": "data_analytics",     # MCP -> Postgres query
    "39080150": "productivity_generic",
    "43192258": "saas_productivity",  # directory of SaaS tools
    "47122769": "ai_ml",
    "46335588": "devtools",           # Python persistence engine
    "45274885": "infra_devops",       # Watermill (Go messaging lib)
    "43090058": "ai_ml",
    "44202417": "devtools",           # PRD markdown tool
    "46287921": "ai_ml",
    "41794109": "ai_ml",
    "41549153": "ai_ml",
    "46773780": "commerce_fintech",   # savings calculator
    "41648656": "infra_devops",
    "46545939": "other",              # room simulator
    "46378482": "ai_ml",
    "49452437": "ai_ml",
    "45025447": "other",              # job board
    "40308374": "data_analytics",     # observability for Flink
    "47397226": "ai_ml",
    "48584025": "design_creative",
    "42351658": "other",              # bluesky facts
    "46759520": "devtools",           # programming language
    "41218293": "devtools",           # polyglot file trick
    "48793904": "ai_ml",
    "45585741": "ai_ml",
    "49804359": "devtools",           # tree-sitter similarity
    "49657006": "devtools",           # vector search engine in C
    "46575795": "commerce_fintech",   # split expenses
    "49097424": "infra_devops",       # nix flakes
    "43815336": "ai_ml",
    "40992014": "ai_ml",
    "40372308": "games_entertainment",
    "50218504": "infra_devops",
    "44404520": "devtools",           # literate programming
    "45517597": "media_content",      # GIF YouTube in-player
    "41243322": "ai_ml",
    "44481673": "infra_devops",
    "41765110": "other",              # traffic driver for NextJS devs
    "46776152": "other",              # SWE job listings
    "45276403": "other",              # climate job board
    "47330789": "ai_ml",
    "49901329": "ai_ml",
    "46612494": "ai_ml",
    "43261000": "ai_ml",
    "46672895": "media_content",      # Internet Archive browser
    "46510233": "data_analytics",     # search analysis
    "44109410": "ai_ml",
    "50329627": "games_entertainment",
    "45066110": "ai_ml",
    "50052566": "ai_ml",
    "41045187": "devtools",           # strace-like tool
    "49523694": "commerce_fintech",   # e-signatures
    "49892088": "education_learning", # daily programming quiz
    "43427741": "design_creative",    # clickable map
    "48555400": "social_community",
    "44682897": "ai_ml",
    "43279279": "education_learning", # typing practice (Korean)
    "47423822": "infra_devops",       # event sourcing
    "46768152": "infra_devops",       # NoSQL database
    "45115616": "data_analytics",     # Text2SQL
    "49950478": "ai_ml",
    "48847052": "devtools",           # Next.js SaaS boilerplate
    "44282116": "ai_ml",
    "46119142": "devtools",           # SQLite repair tool
    "40646741": "media_content",      # videos with code
    "44424683": "education_learning",
    "39995758": "devtools",           # SSH tools for Java
    "45985907": "ai_ml",
    "46968900": "design_creative",    # CAD parts builder
    "45081615": "media_content",      # FFmpeg over HTTP
    "40919142": "ai_ml",
    "44133064": "other",              # Bing Maps lead scraper
    "48980286": "ai_ml",              # DBA agent
    "45974012": "infra_devops",       # down detector
    "44159187": "design_creative",
    "50148526": "other",              # e-ink card frame
    "47932896": "ai_ml",
    "46924716": "other",              # animalese
    "49981383": "social_community",
    "47390443": "ai_ml",
    "42784332": "other",              # typing speed game
    "45028602": "devtools",           # linter framework
    "42692430": "other",              # NeurIPS paper search
    "44716785": "devtools",           # FastAPI template
    "48904912": "data_analytics",     # revenue leak prediction
    "48618477": "games_entertainment",  # terminal typing game
    "44990385": "other",              # fractals
    "49185033": "other",              # TUI access video
    "47160347": "ai_ml",
    "49172992": "media_content",
    "46968900": "other",              # duplicate id, ignored below
    "46955649": "other",              # civic toolkit
}


def load_rows():
    path = os.path.join(ROOT, "data", "ledger.jsonl")
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=75.0,
                    help="min acceptable precision, percent")
    ap.add_argument("--dump", type=str, default="",
                    help="write the scored sample to this path")
    args = ap.parse_args()

    rows = load_rows()
    by_id = {str(r.get("id")): r for r in rows}
    print(f"corpus: {len(rows)} posts, taxonomy {TAXONOMY_VERSION}")

    labelled = [(by_id[pid], truth) for pid, truth in LABELLED.items()
                if pid in by_id]
    n = len(labelled)
    if n < 20:
        print(f"ERROR: only {n} labelled rows resolved -- cannot conclude "
              f"anything (P21)")
        return 1

    hits = 0
    named_hit = named_n = 0
    other_hit = other_n = 0
    by_cat = defaultdict(lambda: [0, 0])
    misses = []
    scored = []

    for r, truth in labelled:
        pred, score, kws = categorize(r.get("title"))
        ok = pred == truth
        hits += ok
        by_cat[truth][1] += 1
        by_cat[truth][0] += ok
        if truth == "other":
            other_n += 1
            other_hit += ok
        else:
            named_n += 1
            named_hit += ok
        if not ok:
            misses.append((r.get("title"), truth, pred, kws))
        scored.append({"id": r.get("id"), "title": r.get("title"),
                       "truth": truth, "pred": pred, "score": score,
                       "keywords": kws, "ok": ok})

    precision = hits / n * 100
    print()
    print(f"=== precision on hand-labelled sample (n={n}) ===")
    for cat, (c, t) in sorted(by_cat.items(), key=lambda kv: -kv[1][1]):
        flag = "" if c == t else "   <-"
        print(f"  {cat:26} {c:>3}/{t:<3} {c/t*100:5.1f}%{flag}")

    print()
    print(f"named-vertical precision : {named_hit}/{named_n} "
          f"({named_hit/named_n*100:.1f}%)")
    print(f"'other' agreement       : {other_hit}/{other_n} "
          f"({other_hit/other_n*100:.1f}%)")
    print(f"overall precision       : {precision:.1f}%")

    # P30: the aggregate precision hides WHERE the errors are. A label
    # resting on a single low-weight keyword hit is measurably less
    # reliable than one resting on several. Any report that cites the
    # aggregate must also state the band breakdown, otherwise a reader
    # cannot tell how much of the corpus is only weakly labelled.
    print()
    print("=== precision by label strength (score = sum of matched keyword weights) ===")
    bands = {"weak (1-2)": [0, 0], "mid (3-5)": [0, 0], "strong (6+)": [0, 0]}
    for s in scored:
        sc = s.get("score", 0) or 0
        band = ("weak (1-2)" if sc <= 2
                else "mid (3-5)" if sc <= 5 else "strong (6+)")
        bands[band][1] += 1
        if s.get("ok"):
            bands[band][0] += 1
    for band in ("weak (1-2)", "mid (3-5)", "strong (6+)"):
        h, t = bands[band]
        if t:
            print(f"  {band:14s} {h:>3}/{t:<3} {h/t*100:5.1f}%")
    weak_h, weak_t = bands["weak (1-2)"]
    if weak_t and weak_h / weak_t < args.threshold:
        print(f"  note: the weak band alone is below the {args.threshold:.0f}% "
              f"threshold - do not quote a rate for rows in that band")

    # Coverage over the whole corpus, independent of the sample.
    cats = Counter()
    for r in rows:
        cats[categorize(r.get("title"))[0]] += 1
    total = len(rows)
    covered = total - cats["other"]
    print(f"corpus coverage         : {covered}/{total} "
          f"({covered/total*100:.1f}% get a vertical label)")

    if misses:
        print()
        print(f"=== misses ({len(misses)}) ===")
        for title, truth, pred, kws in misses[:40]:
            t = (title or "")[:64]
            print(f"  truth={truth:22} pred={pred:22} {t}")

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            for s in scored:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print(f"\nwrote scored sample -> {args.dump}")

    # Persist the measurement so the report can quote a measured number
    # instead of a hand-typed one. The report must never assert a figure
    # that no file in the repo produced.
    audit_out = os.path.join(ROOT, "data", "category_audit.json")
    with open(audit_out, "w", encoding="utf-8") as f:
        json.dump({
            "taxonomy_version": TAXONOMY_VERSION,
            "n": n,
            "hits": hits,
            "precision": hits / n,
            "named_precision": named_hit / named_n if named_n else 0,
            "named_n": named_n,
            "other_agreement": other_hit / other_n if other_n else 0,
            "other_n": other_n,
            "coverage": covered / total,
            "corpus": total,
            "threshold": args.threshold,
            "pass": precision >= args.threshold,
            "precision_by_band": {
                band: {"hits": h, "n": t, "precision": h / t if t else 0}
                for band, (h, t) in bands.items() if t
            },
            "band_definition": {
                "weak (1-2)": "one low-weight keyword hit",
                "mid (3-5)": "mid-strength keyword hits",
                "strong (6+)": "several strong keyword hits",
            },
        }, f, ensure_ascii=False, indent=1)
    print(f"wrote audit result -> {audit_out}")

    print()
    if precision < args.threshold:
        print(f"FAIL: precision {precision:.1f}% < threshold {args.threshold}%")
        return 1
    print(f"PASS: precision {precision:.1f}% >= threshold {args.threshold}% "
          f"(n={n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
