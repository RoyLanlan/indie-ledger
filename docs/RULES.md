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
