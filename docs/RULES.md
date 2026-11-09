# indie-ledger SKILL

**Purpose**: Run a reproducible ledger of indie products launched via Show HN,
tracking which are still alive and which are gone.

Every rule here was derived from a measured failure on 2026-11-09, not assumed.
Where a rule exists, it exists because the naive version of it was measured wrong.

---

## 1. Hard red lines

1. **Every published number must trace to a row in `data/ledger.jsonl` or
   `data/repo_status.jsonl` or `data/liveness.jsonl`.** If you cannot point at
   the row, do not publish it.
2. **Never publish a cohort with fewer than 30 entries.** Sample size goes in
   the table, not in a footnote.
3. **A verdict without evidence is not publishable.** Every verdict carries
   `reason` and the raw signal that produced it.
4. **"No longer maintained" is not "failed."** A project may have been
   deliberately finished. The wording is `not maintained`, never `failed`.
5. **Never promise revenue.** MRR and user counts are third-party or
   self-reported facts with sources and dates, or they are omitted.
6. **State the population explicitly.** This dataset covers *products whose
   authors posted them to Show HN*. It does not cover all indie developers,
   and it over-represents developers who build in public and speak English.
7. **A removed entry is not a death.** If a maker asks to be delisted, the row
   is marked `removed` and excluded from every rate.
8. **Same input, same output.** Re-running the analyzer on the same data must
   produce byte-identical output. No timestamps inside the report body.
9. **Never claim a rate covers the whole industry.** It covers a cohort.

---

## 2. Collection rules (L1)

| Rule | Why |
|---|---|
| Query Show HN **one calendar day at a time** | Algolia caps reachable hits at 1000 per query. A year window reports `nbHits=28,246` but returns only the newest 1000, silently. |
| Always pass **both** date bounds | `numericFilters=created_at_i>{lo},created_at_i<{hi}`. A single lower bound is ignored and returns the entire corpus (~507k). |
| Use `tags=show_hn`, not `tags=story` | `tags=story` without `query` returns empty. This was the source of the old "Algolia is unreliable" note — it was wrong. |
| Use UTC midnight for day boundaries | The host runs CST; local midnight shifts cohorts by 8 hours and corrupts monthly and yearly groupings. |
| Store per-day files plus a rebuilt ledger | A partial run must never corrupt prior data. The ledger is rebuilt from day files, deduped by HN `objectID`. |

**Host classification** (measured on 88,874 posts). `product_site` is kept.
Everything else is classified out:

```
platform        google.com, huggingface.co, apple.com, npmjs.com, pypi.org, ...   2%
hosting         *.vercel.app, *.workers.dev, blogspot.com, itch.io, surge.sh, ...   5%
content_host    medium.com, substack.com, dev.to, hashnode.dev                    0.2%
github          github.com/<owner>/<repo>                          31%
product_site    everything else                                    55%
```

`google.com` appeared 23 times in a 14-day sample alone. Without this filter
the ledger is mostly platforms, not products.

---

## 3. GitHub-side verdict rules (L2a)

For each repo in the ledger:

| Signal | Verdict |
|---|---|
| `GET /repos/{owner}/{repo}` → 404 | **dead** — repo deleted |
| `archived == true` | **archived** — read-only, still exists; not maintained, not dead |
| `pushed_at` older than 365 days | **dormant** |
| `pushed_at` 180–365 days | **slowing** |
| `pushed_at` within 180 days | **active** |
| → 451 | **unavailable** — blocked for legal reasons; repo still exists |
| → 403 "Repository access blocked" | **repo_blocked** — ToS/DMCA takedown; repo still exists |
| → 403 / 429 quota, or abuse throttle | *no verdict* — retry later, never a death |

`unavailable` and `repo_blocked` are excluded from both the alive and the dead
numerators and from the rate denominator. `archived` counts toward
"not maintained" but not toward "dead". **Only a genuine 404 is a death.**
See P13–P20.

Measured on the 2024-03 cohort, 2.7 years later (n=40):

```
repo deleted (404)   52%
archived              5%
dormant (>6mo)       20%
active               22%
--------------------------------
not maintained       77%
```

**Prefer GitHub-linked products as the ledger's backbone.** Domain-only
products can be matched to a repo only ~20% of the time (3 search strategies
tested, best 2/10), so their verdicts rest on DNS/HTTP alone, which is much
weaker.

**Do not bother with ETag caching.** Measured: conditional `If-None-Match`
requests returning 304 still consume rate-limit quota (10 conditional calls
cost 11 points). Recurring checks are not free.

**Quota**: unauthenticated is 60 core requests/hour — 24,767 repos would take
~17 days. With a PAT (5000/hr) the same set takes ~5 hours. A PAT is a
prerequisite for the full sweep; the sampler runs unattended without one and
upgrades automatically when a token appears.

---

## 4. Domain-side verdict rules (L2b)

Naive "HTTPS failed ⇒ dead" measured **53% precision**. Wrong. The rule order
below was re-measured on 65 domains and is the only version to use.

```
1. NXDOMAIN confirmed twice, ~1s apart              -> dead
2. any HTTP < 400 on port 80 or 443                  -> alive
3. HTTP 403 / 405 / 429                              -> blocked  (site up, refuses bots)
4. DNS resolves, port 80/443 open, request fails     -> degraded
5. DNS resolves, no listener on 80/443               -> dead
```

**The rule that matters most: a TLS failure is not death.**

| Domain | Naive verdict | Correct verdict | Why |
|---|---|---|---|
| `demo.radiantdrift.com` | dead | degraded | expired certificate; site serving |
| `notes.samantha.wiki` | dead | degraded | self-signed certificate; site serving |
| `lych3e.com` | dead | alive | HTTPS cert invalid but HTTP returned 204 |
| `lageb.itch.io` | dead | blocked | 403 — site exists, refuses bots |
| `trytaylor.ai` | dead | blocked | 429 — rate limited, not dead |
| `gptbundle.ai` | dead | alive | TLS alert on 443, HTTP 200 on 80 |

Never classify `blocked` as dead. Never classify a certificate error as death.

Measured distribution, 2024-03 cohort, 2.7 years later (n=65):

```
alive 72%   dead 13%   blocked 9%   degraded 4%
```

---

## 5. Confidence tiers

A verdict is only as good as its signal. Reports must present tiers separately.

| Tier | Source | Usable for |
|---|---|---|
| **high** | GitHub repo state (404 / archived / push age) | primary cohort rates |
| **medium** | Domain DNS + HTTP | corroboration only |
| **none** | collected but inconclusive | excluded from rates, counted as `unverified` |

Never blend tiers into one headline number. The 13% domain-side death rate and
the 52% GitHub-side deletion rate describe **different populations** — GitHub
skews toward developers who publish code; domain-side includes closed-source
tools.

---

## 6. Precedents

Recorded decisions and the incidents that produced them.

### P1 — A certificate error is not a death (2026-11-09)
Incident: initial classifier reported 32% dead on the 2024-03 cohort. Re-checking
all 13 "dead" verdicts found 6 were alive: expired certs, self-signed certs, and
sites that block HEAD. Fix: new rule order, plus a `degraded` and `blocked`
verdict. Result: 32% → 13%.

### P2 — `nbHits` is not a reachable count (2026-11-09)
Incident: a 2025 full-year query reported 28,246 hits; paginating returned
1,000 records and then `nbHits=0`. 96% of the year was silently unavailable.
Fix: day-sliced collection only. A year now costs ~1,100 requests instead of 10.

### P3 — One-sided date filters return everything (2026-11-09)
Incident: `numericFilters=created_at_i>{lo}` with no upper bound returned
507,043 hits — the entire corpus — instead of the requested range. Fix: always
both bounds.

### P4 — `tags=story` is not `tags=show_hn` (2026-11-09)
Incident: an existing skill recorded "Algolia often returns empty results".
Cause: `tags=story` with no `query` genuinely returns empty. `tags=show_hn`
with a date filter returned consistent counts across 5 repeated probes.
Fix: corrected the skill.

### P5 — `removed` is not `dead` (2026-11-09)
Decision: a maker asking for removal produces a `removed` verdict, excluded from
all rates. Removing a row must not improve the survivor statistics.

### P6 — "No longer maintained" is the correct wording (2026-11-09)
Decision: reports say `not maintained`, never `failed`. Independently phrased
and validated by an existing public directory using the same wording:
"A non-maintained product isn't a failure — many were deliberately finished
rather than abandoned."

### P7 — Do not match products to repos by domain search (2026-11-09)
Incident: 3 search strategies over 10 domains yielded a 20% strong-match rate.
Decision: domain→repo matching is not a primary enrichment path. Where a repo
cannot be identified, the product stays in the medium tier.

### P8 — The population is Show HN posters, not indie developers (2026-11-09)
Decision: every report states the population in its first paragraph. The
dataset over-represents developers who build in public, use GitHub, and post in
English. It is a valid cohort; it is not the industry.

### P9 — ETag 304 still costs quota (2026-11-09)
Incident: assumed conditional requests would make recurring checks free.
Measured: 10 conditional requests consumed 11 quota points. Decision: dropped
ETag caching; use PAT-based throughput instead.

### P10 — Platform hosts must be filtered (2026-11-09)
Incident: `google.com` appeared 23 times, `huggingface.co` 17, `apple.com` 16
in a 14-day sample. Decision: explicit host taxonomy; only `product_site` and
`github` enter the ledger.

### P11 — Never blend confidence tiers in one table (2026-11-09)
Incident: the report printed "高置信层整体：63% 已死" while its own table on
the same page said 21%. Cause: the headline used the global verdict counter
instead of the high-tier subset counter. Fix: tier counters are computed from
the tier's own filtered list; the renderer renders one table per tier.

### P12 — Percentages need the right denominator and precision (2026-11-09)
Incident: integer division rendered a verification rate of 51/12,383 as "0%".
Fix: `dead_pct_verified` and `not_maintained_pct_verified` use the verified
subset as denominator (unverified rows never enter a rate); rates below 1%
render with one decimal.

### P13 — A failed request is never evidence of death (2026-11-09)
Incident: `api()` returned `None` both for a genuine 404 and for a request
that exhausted its retries against a rate limit. `classify_repo(None)` then
classified every one of those as `dead`. Measured contamination: of 25
sampled "404 deleted" rows, 3 repos were alive and pushing — about 12% false
death rate. Fix: `api()` raises `RateLimitError` when retries are exhausted
and returns `None` **only** on a real 404. Callers catch the exception, write
no verdict, and leave the repo queued for the next pass. Regression test:
`test_false_deaths.py`.

### P14 — HTTP 451 is not a 404 (2026-11-09)
Incident: `Anil-matcha/Free-GPT-Actions` returns HTTP 451 (repo blocked for
legal reasons). A 451 repo still exists; it is not deleted. Fix: distinct
verdict `unavailable`, excluded from both the alive and dead numerators and
from the rate denominator.

### P15 — A secondary rate limit does not consume core quota (2026-11-09)
Incident: the sampler stopped making progress while the quota readout stayed
at 4128/5000. Cause: GitHub's abuse detection returns 403 **without**
spending core quota, so a sampler that polls the quota to decide whether to
sleep waits forever while its own burst pattern keeps it blocked. Fix:
distinguish `SecondaryRateLimit` from quota exhaustion by reading the
response body, back off long (300s), and raise the inter-request delay to
0.35s. Rule: never infer "I am throttled" from the quota counter alone.

### P16 — HTTP 403 has three unrelated meanings (2026-11-09)
Incident: `PublicAffairs/openai-github-copilot` (ToS block) and
`halpz/re3` (DMCA block) both return 403. The sampler classified any 403 as
a rate limit, slept 90s per repo, and eventually wrote a false death. Fix:
`_classify_403()` reads the body and returns `quota` / `secondary` /
`blocked` / `unknown`. A `blocked` repo raises immediately with no retry and
is recorded as verdict `repo_blocked` (see P17) — still excluded from the rate
denominator,
same as `unavailable`. Rule: never map a non-404 error to a death verdict.

### P17 — Two tiers must never share a verdict name (2026-11-09)
Incident: the GitHub tier started emitting a verdict called `blocked` for
repositories taken down for ToS/DMCA reasons. The domain tier already used
`blocked` for "the site answered 403/405/429" — which means the site is
**alive** and merely refusing bots. Collapsing the two names put live sites
into the "unavailable" bucket and silently dropped them out of the
denominator. Fix: the repo tier emits `repo_blocked`; the domain tier keeps
`blocked` and counts it as alive. Rule: a verdict name has one meaning across
the whole ledger — when two layers need the same word for different things,
rename one of them.

### P18 — One writer per data file (2026-11-09)
Incident: the sampler appends to a shared JSONL. A long-running standalone
instance and the cron-triggered instance both wrote to it, which would
interleave lines and double-count repos. `pipeline.py`'s own stage lock did
not help, because the standalone instance ran outside that lock. Fix: the
sampler takes its own `flock` (`.locks/github_sampler.lock`) and exits
immediately if another instance holds it. Rule: any component that appends to
a shared file must hold its own lock, not rely on the caller's.

### P19 — Throughput must come from concurrency, never from faster verdicts (2026-11-09)
Incident: the domain scan ran at 11.5 domains/min, so the full 36,881-domain
sweep needed ~52 hours. The per-domain cost was dominated by DNS timeouts
and a `dig` subprocess, not by the politeness delay. Measured on a 60-domain
sample: serial 36.6/min, 16 threads 196.8/min; in production 239/min, i.e.
**ETA 2.5 hours instead of 52**. The verdict rules were NOT changed — a
40-domain A/B run agreed 40/40 between the serial and threaded paths, and a
regression test now pins that equivalence. Rule: when a sweep is too slow,
parallelise it and prove the output is unchanged; never loosen the rules to
make the number look better.

### P20 — Archived is not dead (2026-11-09)
Incident: `archived == true` was mapped to the `dead` verdict. An archived
repository still exists, its code is still there, its stars are still there —
the author simply marked it read-only. Measured on the live ledger: **100 of
207 rows in the `dead` bucket were archived repos, so the death count was
inflated by ~46%.** A 25-row sample of "dead" rows returned 404 for only 10;
the other 15 were alive-and-archived. Some were archived only days after
their last push (e.g. one pushed 2026-10-31 and was archived shortly after).
Fix: `archived` is its own verdict. It counts toward "not maintained" and NOT
toward "dead". **Only a genuine 404 means the repo is gone.** This matches the
project's own rule that `archived`, long-no-push and deletion must stay
separate. Effect on the published rate: death 8% -> 4%, not-maintained 69%.

### P21 — An audit with no signal must not report PASS (2026-11-09)
Incident: `audit_deaths.py` ran mid-token-rotation. 24 of 30 lookups returned
HTTP 401 (invalid credentials) and were counted as "inconclusive", but the
script still printed `PASS: false-death rate 0.0%` — a pass computed over the
6 rows that happened to succeed. A broken audit that reports success is
worse than no audit, because it silences the alarm. Fix: the audit fails when
the inconclusive share exceeds a limit (default 20%), on the grounds that a
false-death rate measured over a mostly-failed sample proves nothing. The
same rule applies to any periodic health check: **report the coverage of the
check alongside its result.**

### P22 — Audit the death verdict against the live API, not against tests (2026-11-09)
Incident: the archived-vs-dead bug (P20) survived the whole unit-test suite,
because the test asserted the buggy behaviour ("archived is dead"). Only
re-checking real `dead` rows against the live API exposed it: 100 of 207 were
archived repos. Fix: `audit_deaths.py` samples the `dead` bucket, asks
GitHub for each repo, and reports the share that is not actually a 404. A
death verdict is valid only if the API says 404. Measured after the P20 fix:
24 of 25 sampled rows confirmed 404, 1 leftover archived row repaired.

---

### P23 — A category rate is not a finding until a test says so (2026-11-09)
Incident: the first vertical table showed `commerce_fintech` at 22.2% dead
against a 4.8% baseline and read like a headline finding. It survived one
of two confounds (it was not a demo-repo artifact, it was not
corporate-maintenance artifact), but the table still printed a row per
vertical with no indication of which differences were distinguishable from
sampling noise. Fix: every vertical rate is now paired with an exact
one-sided binomial tail against its own launch-year baseline, and the
report bolds only verticals that pass p < 0.05. Measured on the current
snapshot: exactly one vertical passes (commerce_fintech, 2024 cohort,
p = 0.00004); every other vertical is statistically indistinguishable
from the cohort baseline. Rule: a rate that has not been tested is a
number, not a conclusion.

### P24 — Classifier quality is measured on hand labels, never asserted (2026-11-09)
Incident: the first deterministic classifier scored 52.5% precision against
hand labels and would have been published as a taxonomy. Coverage was
misread as quality — the taxonomy covered 70%+ of rows while being wrong
half the time. Fix: `audit_categories.py` scores the classifier against a
sampled, hand-labelled set and reports precision, named-vertical precision
and coverage separately; the report prints those measured numbers, and the
classifier only ships if the audit passes. Two follow-up rules came out of
the same work: generic words (`app`, `tool`, `client`) must never decide a
category on their own, and coverage may never be quoted as a quality
metric.

### P25 — Pooling launch years into one rate is a confounded comparison (2026-11-09)
Incident: the pooled vertical table compared a 2024 cohort (2.8 years of
exposure) against 2026 cohorts (months of exposure) in one denominator, so
the rate for any vertical was mostly a function of when it launched. Fix:
vertical tables are always cut by launch year, and the comparison baseline
for the significance test is the same launch year's cohort rate, never the
pooled rate.

### P27 — A credential that silently fails to bind is worse than one that errors (2026-11-09)
Incident: the GitHub sampler died with an unhandled `HTTPError: 401`. Two
separate defects were behind it. First, `api()` had no 401 branch at all:
the error fell through to the generic retry path, retried three times, then
escaped as an unhandled exception and killed the run mid-batch. Second and
worse, `--pat-file` never had any effect. `github_sampler.main()` did
`global TOKEN; TOKEN = t`, which rebinds the name inside `github_sampler`,
but `api()` lives in `github_enrich` and reads `github_enrich.TOKEN` - a
different binding. The sampler therefore always sent whatever token
`github_enrich` loaded at import time, so a rotated token never took
effect and a revoked token kept the sampler running until every request
returned 401. Fixes: (a) `api()` raises a dedicated `AuthError` on 401 -
never retried, because the same request fails identically - and the
sampler, the repair script and the audit all stop cleanly with exit code 2
and record no verdict; (b) the sampler installs the token in
`github_enrich`, where `api()` actually reads it; (c) `pipeline.py`
propagates exit 2 instead of reporting a normal run. Rule: a credential
path that cannot be proven to reach the HTTP layer is not a working
credential path.

### P28 — An audit must abort on a rejected token, not merely count it (2026-11-09)
P21 already required an audit to fail when too many lookups were
inconclusive. A 401 deserves a stronger response than being counted as
one inconclusive row: every subsequent request would fail the same way, so
the audit now aborts on the first 401, reports that it has no signal, and
changes nothing on disk. Rule: an audit that cannot authenticate has no
result, and a result it cannot produce is not a PASS.

### P29 — A rate needs enough observations behind it (2026-11-09)
Incident: a stale audit run reported `PASS: false-death rate 0.0%
(threshold 5.0%)` while 24 of its 30 lookups had failed with 401. The
0.0% was computed over the 6 rows that did come back conclusive - far too
few to detect the 5% rate the audit exists to catch. (The run itself
executed a pre-P21 copy of the script, so this was a reporting failure
rather than a live one, but the guard was missing all the same.) Fix: the
audit now refuses to report a rate at all unless it has at least
`--min-conclusive` (default 20) conclusive lookups, and the PASS line
always prints how many rows the rate was computed over. Rule: a percentage
over a handful of observations is not a measurement, and a tool that
publishes one must be made to say how many observations it had.

### P30 — An aggregate accuracy hides WHERE the errors are (2026-11-09)
The category audit reported a single number: `precision 83.3% (n=120)`.
But the errors are not spread evenly. Measured on the same hand-labelled
sample, precision by label strength is:

| label strength | meaning | sample | precision |
|---|---|---|---|
| weak (1-2) | one low-weight keyword hit | 51 | 76.5% |
| mid (3-5) | mid-strength keyword hits | 46 | 87.0% |
| strong (6+) | several strong keyword hits | 23 | 91.3% |

61.3% of the corpus is weak-labelled, so most of the corpus sits in the
least reliable band, and the aggregate says nothing about it. Fix: the
audit now prints and stores the band breakdown, and the report shows it as
table 5.0 with an explicit note that a vertical's true precision depends
on its label composition, so a rate for a weak-label-heavy vertical is a
**lower bound**. Rule: an accuracy figure must be reported with the
breakdown of the cases it aggregates over; a single mean over a
heterogeneous population is not usable on its own.

Also fixed in the same pass, from a manual read of the ten dead
`commerce_fintech` rows behind the report's one significant finding:
three of them were not commerce at all - "GitHub App to Streamline
Software Delivery" is developer tooling, "Interactive Delivery Zone
Geolocation" is logistics, "CxReports - Automate Delivery" is reporting.
The keyword `delivery` was doing the damage. Same class of fault for
`calculator`, `token`, `extract` and `graph`: each was a generic English
word that matched a vertical but described something else. They were
replaced with specific phrases (`food delivery`, `compound interest`,
`crypto token`, `llm extract`, `graph database`, ...). That moved 527 of
88,874 rows (0.6%) to a correct label or to `other`.

Note the honest result: overall precision stayed at 83.3%. The fixes
corrected *semantics* that the 120-row sample was too small to detect.
That is a real improvement and also a measurement that the sample cannot
see - which is why the sample size itself is a tracked limitation.

### P31 — A crashed stage must not look like an unchanged stage (2026-11-09)
The report stage died with `OverflowError: int too large to convert to
float`, thrown from the exact binomial tail: `math.comb(n, k)` returns an
exact int that cannot be converted to float once n reaches the
thousands. The renderer writes its output only at the very end, so the
crash left the **previous** report on disk, unchanged, at the same byte
count. Read casually, that looks exactly like "the report regenerated and
nothing changed" - which is how it was first mistaken for a no-op.

Two rules follow. First, the arithmetic must not overflow: the tail is
now computed in the log-gamma form (`lgamma(n+1) - lgamma(k+1) -
lgamma(n-k+1)`), cross-checked against the exact integer method on
small n (relative error < 1e-13) and verified not to raise on n up to
4000. Second, and independent of the arithmetic: **a stage that fails
must be distinguishable from a stage that produced no change.** A
regeneration should verify that the output file was actually rewritten,
not merely that it exists and looks plausible. A crash that leaves
stale output in place is worse than a crash that leaves nothing, because
it converts a visible failure into an invisible one.

### P32 — Test many hypotheses and correct for it (2026-11-09)
Fifteen verticals are tested per year. Under a bare `p < 0.05`, testing
that many hypotheses produces roughly one "significant" result by chance
alone. The audit caught this in the act: 2025 `education_learning` came
out at `p = 0.0486`, which is under 0.05, and the report was about to
publish it as a second finding alongside the 2024 commerce result.

It does not survive correction. With Holm-Bonferroni across the 15
verticals tested in that cohort, `p = 0.0486` becomes `p = 0.729` -
i.e. entirely ordinary. The 2024 `commerce_fintech` result survives:
`p = 0.0027` raw, `p = 0.0377` corrected, still significant.

Fix: `analyze.py` now computes the Holm-Bonferroni step-up correction
across the set of verticals tested in the same cohort - order by
p-value, the i-th hypothesis gets threshold `0.05/(m-i)`, and a
hypothesis only counts if every one ranked above it also cleared its
threshold. Both the raw and the corrected p-value are reported, and the
report table has a `判定` column. A vertical that passes the raw test
but fails the correction is written up as **待观察** ("worth watching"),
never as a finding.

Rule: a p-value means nothing without knowing how many hypotheses were
tested. If the number of tests is not stated alongside the p-value, the
p-value is not a result - it is a coincidence waiting to be published.

### P33 — A status line in a report must come from the data (2026-11-09)
The report carried this sentence as a hardcoded string: *"2024 and 2025
are 100% verified, 2026 has not started"*. When the domain scan
finished, 2026 was in fact at 61% verified - and the sentence still said
nothing had been checked for it. The report was wrong in the exact place
where a reader looks to decide how much to trust the numbers next to it.

Fix: the sentence is now computed from the same cohort rows the tables
are built from, and it distinguishes three states - fully verified,
partially verified (stating the rate), and not started. Rule: in a
document that regenerates on a schedule, any statement about the
*current* state must be derived at render time. A hardcoded status line
is a lie waiting for the data to move.

### P35 — Count the unit you are describing (2026-11-09)
The domain-completeness note said "38,617 个域名全部扫描完毕" but the
number it computed was 36,877 — the **product** count, not the domain
count. One domain can host several Show HN posts, so the two are
different units and the sentence described domain coverage while
measuring products.

The GitHub tier had the mirror-image bug: its completeness was derived
from cohort rows (products), while the thing actually being verified is
**repos** — 24,767 distinct repos, against 27,958 products that
reference one.

Rule: before writing "X of Y are verified", check that X and Y count
the same kind of thing. Domains vs products vs repos are three
different populations and their counts are not interchangeable. Both
completeness sentences now read their totals directly from the ledger
and the verdict files.

A second clause in the same note — "unlike the GitHub layer which is
still being sampled" — was hardcoded and became false the moment the
sampler finished. Same defect as P33, and the reason P33 exists: state
about the pipeline's current condition are computed at render time.

---

## 7. Pipeline contract

```
L1  collect   day-sliced Show HN  -> data/days/YYYY-MM-DD.json  + data/ledger.jsonl
L2a github    repo state           -> data/repo_status.jsonl
L2b domain    DNS + HTTP           -> data/liveness.jsonl
L3  analyze   join + cohort        -> data/products.json
L4  report    deterministic render -> reports/YYYY-Www.md
```

Each layer is resumable and idempotent. Re-running L1 on a date already
fetched does not duplicate rows. L3 refuses to publish cohorts under 30.

**Scheduling** (once the operation is stable):
- L1 daily at 03:23 UTC — catches yesterday's posts, avoids the UTC midnight
  boundary where Show HN volume peaks
- L2a hourly — 60 repos/hour unauth, 5000 with a PAT
- L2b continuous — 16 workers, ~240 domains/min, no quota
- L3 + L4 weekly — Monday 09:07

---

## 8. Operating cost

| Layer | Cost |
|---|---|
| L1 | ~1,100 Algolia requests per year of history; 3 per day thereafter. No auth, no documented limit. |
| L2a | 24,767 repo checks. Unauth: ~17 days. PAT: ~75/min -> ~5.5 hours. |
| L2b | 36,881 domains at ~240/min (16 workers) = ~2.5 hours, one pass, no quota. |
| L3/L4 | Local compute. LLM is not required for any verdict. |
| LLM | Optional, only for positioning-sentence extraction and post-mortem narrative. Zero if skipped. |

No layer requires a paid API. The operation's cost is dominated by GitHub
quota, which a PAT removes.

---

## 9. Operating notes (2026-11-09)

- PAT confirmed working for user `RoyLanlan`: core 5000/hr, search 30/min.
- Full GitHub sweep measured at ~86 repos/min -> ~5 hours for 24,767.
- Domain scan measured at 11.5 domains/min (DNS resolution dominates the
  1s delay) -> ~53 hours for 36,881 domains, not the ~14 estimated earlier.
- Both sweeps are append-only and resumable; neither loses prior data.
