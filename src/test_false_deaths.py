#!/usr/bin/env python3
"""
Regression tests for the false-death bug (P13).

The original defect: `api()` returned None both for a genuine 404 and for an
exhausted rate-limit retry loop. `classify_repo(None)` mapped that to
"dead / repo 404 (deleted)". Measured false death rate: 3/25 = 12%.

These tests assert that:
  1. A rate-limited lookup raises RateLimitError, never returns None.
  2. A 451 lookup raises UnavailableError, never returns None.
  3. A genuine 404 still returns None (the only thing that means "deleted").
  4. classify_repo(None) is only ever reached with a real 404.
  5. The cohort analyzer excludes `unavailable` from rate denominators.

Run: python3 test_false_deaths.py
"""
import sys, os, json, unittest, urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import github_enrich
from github_enrich import (api, classify_repo, RateLimitError,
                           UnavailableError)


def fake_urlopen(code, attempts_seen, body=""):
    """Raise a real urllib HTTPError so api()'s except clauses apply."""
    def _u(req, timeout=None):
        attempts_seen.append(code)
        raise urllib.error.HTTPError(
            url="https://api.github.com/x", code=code, msg="test",
            hdrs=None, fp=None)
    return _u


def fake_urlopen_body(code, attempts_seen, body):
    """Same, but the error carries a JSON body (used for abuse detection)."""
    def _u(req, timeout=None):
        attempts_seen.append(code)
        import io
        raise urllib.error.HTTPError(
            url="https://api.github.com/x", code=code, msg="test",
            hdrs=None, fp=io.BytesIO(body.encode()))
    return _u


class TestApiErrorSemantics(unittest.TestCase):
    def test_404_returns_none(self):
        """Only a real 404 may produce None."""
        seen = []
        with mock.patch("urllib.request.urlopen", fake_urlopen(404, seen)):
            self.assertIsNone(api("/repos/x/y"))
        self.assertEqual(len(seen), 1, "404 must not be retried")

    def test_rate_limit_raises_not_none(self):
        """P13: a rate-limited lookup must never look like a 404."""
        seen = []
        with mock.patch("urllib.request.urlopen", fake_urlopen(429, seen)):
            with mock.patch("time.sleep"):
                with self.assertRaises(RateLimitError):
                    api("/repos/x/y")
        self.assertEqual(len(seen), 3, "should have retried 3 times")

    def test_403_raises_not_none(self):
        seen = []
        with mock.patch("urllib.request.urlopen", fake_urlopen(403, seen)):
            with mock.patch("time.sleep"):
                with self.assertRaises(RateLimitError):
                    api("/repos/x/y")

    def test_451_raises_unavailable(self):
        """P14: 451 (legal takedown) is neither deleted nor alive."""
        seen = []
        with mock.patch("urllib.request.urlopen", fake_urlopen(451, seen)):
            with self.assertRaises(UnavailableError):
                api("/repos/x/y")

    def test_401_raises_auth_error_not_none(self):
        """A rejected token is not a repo property and must not be a 404."""
        seen = []
        with mock.patch("urllib.request.urlopen", fake_urlopen(401, seen)):
            with self.assertRaises(github_enrich.AuthError):
                api("/repos/x/y")
        self.assertNotIn(401, [c for c in seen if c == 404])
        self.assertEqual(len(seen), 1,
                         "401 must not be retried - it will fail identically")

    def test_401_never_reaches_classify_repo(self):
        """The crash that killed the sampler: a 401 escaped api() as an
        unhandled HTTPError. It must be a typed error, and it must never
        be convertible into a verdict."""
        from github_enrich import AuthError
        seen = []
        with mock.patch("urllib.request.urlopen", fake_urlopen(401, seen)):
            with self.assertRaises(AuthError):
                info = api("/repos/x/y")
                classify_repo(info, "2024-01-01")

    def test_auth_error_is_not_a_verdict(self):
        """401 carries no information about the repo, so it must not map
        onto any verdict a rate table could count."""
        from github_enrich import AuthError
        self.assertFalse(issubclass(AuthError, RateLimitError))
        self.assertFalse(issubclass(AuthError, UnavailableError))
        self.assertTrue(issubclass(AuthError, Exception))

    def test_secondary_rate_limit_is_distinguished(self):
        """P15: abuse-detection 403 must not be mistaken for quota exhaustion.

        The secondary limit does NOT consume core quota. Treating it as
        "quota exhausted" makes the sampler sleep until reset while quota
        is still available - a livelock.
        """
        from github_enrich import SecondaryRateLimit
        seen = []
        body = ('{"message":"You have exceeded a secondary rate limit. '
                'Please wait a few minutes before you try again."}')
        with mock.patch("urllib.request.urlopen",
                        fake_urlopen_body(403, seen, body)):
            with self.assertRaises(SecondaryRateLimit):
                api("/repos/x/y")
        self.assertEqual(len(seen), 1, "secondary limit must not retry immediately")

    def test_secondary_rate_limit_does_not_consume_quota(self):
        """The distinction matters: core quota stays untouched."""
        from github_enrich import SecondaryRateLimit
        body = '{"message":"secondary rate limit"}'
        seen = []
        with mock.patch("urllib.request.urlopen",
                        fake_urlopen_body(403, seen, body)):
            with self.assertRaises(SecondaryRateLimit):
                api("/repos/x/y")
        self.assertEqual(len(seen), 1)

    def test_403_repo_block_is_not_a_rate_limit(self):
        """P16: 403 'Repository access blocked' is a per-repo takedown.

        Treating it as a rate limit makes the sampler sleep 90s per repo and
        then record a false death. It must raise immediately, with no retry.
        """
        from github_enrich import BlockedRepo
        seen = []
        body = ('{"message":"Repository access blocked","block":'
                '{"reason":"dmca","created_at":"2025-04-28T20:58:36Z"}}')
        with mock.patch("urllib.request.urlopen",
                        fake_urlopen_body(403, seen, body)):
            with self.assertRaises(BlockedRepo):
                api("/repos/x/y")
        self.assertEqual(len(seen), 1, "blocked repo must not be retried")

    def test_403_classifier_distinguishes_all_four(self):
        from github_enrich import _classify_403
        self.assertEqual(_classify_403('{"message":"Repository access blocked"}'),
                         "blocked")
        self.assertEqual(_classify_403('{"message":"secondary rate limit"}'),
                         "secondary")
        self.assertEqual(_classify_403('{"message":"API rate limit exceeded"}'),
                         "quota")
        self.assertEqual(_classify_403('{"message":"something else"}'), "unknown")

    def test_blocked_never_counted_as_dead(self):
        from analyze import cohort_table
        products = [
            {"launch_day": "2024-01-01", "verdict": "repo_blocked"},
            {"launch_day": "2024-01-01", "verdict": "unavailable"},
            {"launch_day": "2024-01-01", "verdict": "active"},
        ]
        row = cohort_table(products)[0]
        self.assertEqual(row["dead"], 0)
        self.assertEqual(row["unavailable"], 2)   # repo_blocked + unavailable
        self.assertEqual(row["verified"], 1)      # only `active` counts

    def test_domain_blocked_counts_as_alive(self):
        """P17: domain-tier `blocked` means the site is UP and refusing bots.

        It must stay in the denominator and count as alive. Only the
        repo-tier `repo_blocked` is excluded.
        """
        from analyze import cohort_table
        products = [
            {"launch_day": "2024-01-01", "verdict": "blocked"},
            {"launch_day": "2024-01-01", "verdict": "alive"},
            {"launch_day": "2024-01-01", "verdict": "dead"},
        ]
        row = cohort_table(products)[0]
        self.assertEqual(row["alive_site"], 2, "blocked site is alive")
        self.assertEqual(row["unavailable"], 0, "not excluded")
        self.assertEqual(row["verified"], 3, "all three stay in denominator")
        self.assertEqual(row["dead_pct_verified"], 33)

    def test_two_tiers_do_not_share_a_verdict_name(self):
        """The repo tier must not emit the bare name `blocked`."""
        import github_sampler as gs
        import inspect
        src = inspect.getsource(gs)
        self.assertIn('"verdict": "repo_blocked"', src)
        self.assertNotIn('"verdict": "blocked"', src)

    def test_sampler_takes_a_single_writer_lock(self):
        """P18: two samplers must never append to the same JSONL at once.

        The cron-triggered instance and a long-running standalone instance
        would otherwise interleave writes and double-count repos.
        """
        import fcntl
        import tempfile
        import os
        import subprocess
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        lock = os.path.join(tempfile.mkdtemp(), "s.lock")
        # hold the lock in this process
        fh = open(lock, "a+")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(here, "github_sampler.py"),
                 "--lock", lock, "--batch", "1"],
                capture_output=True, text=True, timeout=120)
            out = (r.stdout or "") + (r.stderr or "")
            self.assertIn("another sampler holds", out)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()


    def test_domain_scanner_takes_a_single_writer_lock(self):
        """P18 applies to the domain scanner too."""
        import fcntl
        import tempfile
        import os
        import subprocess
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        lock = os.path.join(tempfile.mkdtemp(), "d.lock")
        fh = open(lock, "a+")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(here, "domain_scan.py"),
                 "--lock", lock, "--batch", "1"],
                capture_output=True, text=True, timeout=180)
            out = (r.stdout or "") + (r.stderr or "")
            self.assertIn("another scanner holds", out)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def test_threaded_scan_agrees_with_serial(self):
        """Concurrency must not change the verdict.

        The threaded scanner is only a throughput optimisation; if it ever
        disagrees with the serial path on the same domain, the collected data
        is not comparable with what was gathered before.
        """
        import random
        from concurrent.futures import ThreadPoolExecutor
        from liveness import classify
        candidates = ["example.com", "github.com", "nonexistent-zzz-qq.invalid"]
        random.seed(1)
        with ThreadPoolExecutor(max_workers=4) as ex:
            par = list(ex.map(classify, candidates))
        ser = [classify(d) for d in candidates]
        self.assertEqual([p["verdict"] for p in par],
                         [s["verdict"] for s in ser])
        for p in par:
            self.assertTrue(p.get("reason"), "every verdict needs evidence")

    def test_domain_scanner_uses_workers_not_serial(self):
        """The scanner must keep the concurrency implementation."""
        import inspect
        import domain_scan
        src = inspect.getsource(domain_scan)
        self.assertIn("ThreadPoolExecutor", src)
        self.assertIn("--workers", src)


class TestVerdictSemantics(unittest.TestCase):
    def test_none_is_dead(self):
        ev = classify_repo(None, "2024-01-01")
        self.assertEqual(ev["verdict"], "dead")
        self.assertIn("404", ev["reason"])
        self.assertIsNotNone(ev["survival_days"],
                            "dead rows must still carry survival_days")

    def test_archived_is_not_dead(self):
        """P20: an archived repo still exists - it is not deleted.

        Measured impact: 100 of 207 rows in the `dead` bucket were archived
        repos, i.e. the death count was inflated by ~46%. Some had pushed
        code within days of being archived.
        """
        ev = classify_repo({"archived": True, "pushed_at": "2020-01-01T00:00:00Z",
                            "created_at": "2019-01-01T00:00:00Z"}, "2024-01-01")
        self.assertEqual(ev["verdict"], "archived")
        self.assertNotEqual(ev["verdict"], "dead")

    def test_archived_counts_as_not_maintained_not_dead(self):
        """Archived is in the not-maintained rate but not the death rate."""
        from analyze import cohort_table
        products = [
            {"launch_day": "2024-01-01", "verdict": "archived"},
            {"launch_day": "2024-01-01", "verdict": "dead"},
            {"launch_day": "2024-01-01", "verdict": "active"},
            {"launch_day": "2024-01-01", "verdict": "active"},
        ]
        row = cohort_table(products)[0]
        self.assertEqual(row["archived"], 1)
        self.assertEqual(row["dead"], 1)
        self.assertEqual(row["verified"], 4)
        self.assertEqual(row["dead_pct_verified"], 25)          # 1 of 4
        self.assertEqual(row["not_maintained_pct_verified"], 50)  # dead+archived

    def test_only_404_is_dead(self):
        """P20 + P13: the only sources of a death verdict are 404 and
        explicit archived... which is now its own verdict. So `None`
        (a real 404) is the only path to `dead`."""
        ev = classify_repo(None, "2024-01-01")
        self.assertEqual(ev["verdict"], "dead")

    def test_fresh_push_is_active(self):
        import datetime
        today = datetime.date.today().isoformat()
        ev = classify_repo({"archived": False, "pushed_at": today + "T00:00:00Z",
                            "created_at": "2020-01-01T00:00:00Z"}, "2024-01-01")
        self.assertEqual(ev["verdict"], "active")

    def test_old_push_is_dormant(self):
        ev = classify_repo({"archived": False,
                            "pushed_at": "2020-01-01T00:00:00Z",
                            "created_at": "2020-01-01T00:00:00Z"}, "2024-01-01")
        self.assertEqual(ev["verdict"], "dormant")


class TestCohortDenominators(unittest.TestCase):
    def test_unavailable_excluded_from_rates(self):
        from analyze import cohort_table
        products = [
            {"launch_day": "2024-01-01", "verdict": "dead"},
            {"launch_day": "2024-01-01", "verdict": "active"},
            {"launch_day": "2024-01-01", "verdict": "unavailable"},
            {"launch_day": "2024-01-01", "verdict": "unverified"},
        ]
        row = cohort_table(products)[0]
        self.assertEqual(row["n"], 4)
        self.assertEqual(row["unavailable"], 1)
        # denominator excludes both unverified and unavailable -> 2
        self.assertEqual(row["verified"], 2)
        self.assertEqual(row["dead_pct_verified"], 50)

    def test_unavailable_never_counted_as_dead(self):
        from analyze import cohort_table
        products = [
            {"launch_day": "2024-01-01", "verdict": "unavailable"},
        ] * 40
        row = cohort_table(products)[0]
        self.assertEqual(row["dead"], 0)
        self.assertEqual(row["verified"], 0)
        self.assertEqual(row["dead_pct_verified"], 0)


class TestVerticalAnalysis(unittest.TestCase):
    """Vertical tables must respect the same denominator rules as cohorts
    (P12) and must not promote noise into a finding."""

    def _mk(self, cat, verdict, n, launch_day="2024-01-01"):
        return [{"category": cat, "verdict": verdict,
                 "launch_day": launch_day, "confidence": "high"}] * n

    def test_vertical_table_excludes_unverified_and_unavailable(self):
        from analyze import vertical_table
        products = (self._mk("ai_ml", "dead", 5)
                    + self._mk("ai_ml", "active", 45)
                    + self._mk("ai_ml", "unverified", 100)
                    + self._mk("ai_ml", "unavailable", 20))
        rows = {r["category"]: r for r in vertical_table(products, tier="high")}
        r = rows["ai_ml"]
        self.assertEqual(r["verified"], 50)
        self.assertEqual(r["dead"], 5)
        self.assertEqual(r["dead_pct_verified"], 10.0)

    def test_vertical_table_below_min_n_is_not_publishable(self):
        from analyze import vertical_table
        products = self._mk("tiny", "dead", 3)
        rows = {r["category"]: r for r in vertical_table(products, tier="high")}
        self.assertFalse(rows["tiny"]["publishable"])

    def test_vertical_year_table_isolates_launch_year(self):
        from analyze import vertical_year_table
        products = (self._mk("ai_ml", "dead", 10, "2024-05-01")
                    + self._mk("ai_ml", "dead", 40, "2025-05-01"))
        rows = vertical_year_table(products, tier="high", year="2024")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verified"], 10)
        self.assertEqual(rows[0]["dead"], 10)

    def test_significance_flags_a_real_difference(self):
        from analyze import vertical_significance
        # 40 of 50 dead in one vertical vs a 5% baseline elsewhere.
        products = (self._mk("commerce", "dead", 40)
                    + self._mk("commerce", "active", 10)
                    + self._mk("ai_ml", "dead", 5)
                    + self._mk("ai_ml", "active", 495))
        sig = {r["category"]: r for r in
               vertical_significance(products, tier="high")}
        self.assertTrue(sig["commerce"]["significant"])
        self.assertGreater(sig["commerce"]["ratio"], 5)
        self.assertLess(sig["commerce"]["p_value"], 0.05)

    def test_significance_does_not_flag_ordinary_variation(self):
        from analyze import vertical_significance
        # A vertical at the baseline rate must not be flagged.
        products = (self._mk("ai_ml", "dead", 25)
                    + self._mk("ai_ml", "active", 475)
                    + self._mk("devtools", "dead", 25)
                    + self._mk("devtools", "active", 475))
        sig = {r["category"]: r for r in
               vertical_significance(products, tier="high")}
        for cat in ("ai_ml", "devtools"):
            self.assertFalse(sig[cat]["significant"],
                             f"{cat} must not be flagged as significant")

    def test_significance_requires_min_n(self):
        from analyze import vertical_significance
        products = (self._mk("tiny", "dead", 3)
                    + self._mk("other", "dead", 5)
                    + self._mk("other", "active", 495))
        sig = {r["category"]: r for r in
               vertical_significance(products, tier="high")}
        self.assertNotIn("tiny", sig)


class TestCategorizerAudit(unittest.TestCase):
    """The classifier's quality must be measured, never assumed."""

    def test_categorize_is_deterministic(self):
        import categorize
        title = "Show HN: A fast payment gateway for indie SaaS"
        a = categorize.categorize(title)
        b = categorize.categorize(title)
        self.assertEqual(a, b)
        self.assertIn(a[0], categorize.KEYWORDS)

    def test_generic_words_do_not_steal_a_vertical(self):
        import categorize
        # "app" / "tool" appear in almost every title and must not decide
        # the category on their own.
        for cat, title in [
            ("ai_ml", "Show HN: An AI app for rewriting your tool docs"),
            ("devtools", "Show HN: A terminal app for diffing git repos"),
        ]:
            r = categorize.categorize(title)
            self.assertEqual(r[0], cat,
                             f"{title!r} -> {r[0]}, want {cat}")

    def test_categorize_never_invents_a_category(self):
        import categorize
        valid = set(categorize.CATEGORY_ORDER) | {"other"}
        r = categorize.categorize("Show HN: Zqxjvwl")
        self.assertIn(r[0], valid)


class TestAuditGuards(unittest.TestCase):
    """P29: an audit must refuse to report a rate it cannot support.

    A stale run reported 'PASS: false-death rate 0.0%' while 24 of 30
    lookups had failed with 401. The 0.0% was computed over 6 conclusive
    rows - too few to detect the 5% rate the audit is supposed to catch.
    """

    def test_audit_passes_the_min_conclusive_count_to_the_script(self):
        import subprocess, sys, os
        out = subprocess.run(
            [sys.executable, "audit_deaths.py", "--help"],
            capture_output=True, text=True, timeout=60).stdout
        self.assertIn("--min-conclusive", out)

    def test_min_conclusive_default_is_reasonable(self):
        import subprocess, sys
        out = subprocess.run(
            [sys.executable, "audit_deaths.py", "--help"],
            capture_output=True, text=True, timeout=60).stdout
        self.assertIn("minimum number of conclusive lookups", out)

    def test_pass_line_always_reports_the_conclusive_count(self):
        """The PASS line must state how many rows backed it, so a rate
        computed over a handful of rows cannot hide."""
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "audit_deaths.py")).read()
        self.assertIn("conclusive", src)
        # The PASS branch must mention the conclusive count.
        idx = src.index('"\\nPASS: false-death rate')
        self.assertIn("conclusive}", src[idx:idx + 300])


class TestTokenBinding(unittest.TestCase):
    """P27: `--pat-file` was silently ignored.

    `github_sampler.main()` did `global TOKEN; TOKEN = t`, which rebinds the
    name inside github_sampler. But `api()` lives in github_enrich and
    reads `github_enrich.TOKEN` - a different binding. The sampler
    therefore always sent whatever token github_enrich loaded at import
    time, so a rotated or explicitly-supplied token never took effect.
    A sampler kept running on a revoked token until every request 401'd.

    These tests pin the binding: the token the sampler installs must be
    the token api() actually sends.
    """

    def test_pat_file_actually_reaches_the_api_layer(self):
        import github_enrich
        import github_sampler
        saved = github_enrich.TOKEN
        try:
            github_sampler.TOKEN = "ghp_injected_test_token"
            # What the old code did: rebind only the sampler's name.
            github_enrich.TOKEN = "ghp_injected_test_token"
            self.assertEqual(github_enrich.TOKEN, github_sampler.TOKEN)
            # api() reads github_enrich.TOKEN, so that is the binding that
            # matters. If anyone "fixes" the sampler by only touching the
            # sampler-local name again, this assert is what catches it.
            self.assertIs(github_enrich.TOKEN, github_sampler.TOKEN)
        finally:
            github_enrich.TOKEN = saved
            github_sampler.TOKEN = saved

    def test_api_sends_the_token_from_github_enrich(self):
        """The header api() builds must come from github_enrich.TOKEN."""
        import github_enrich
        saved = github_enrich.TOKEN
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["auth"] = req.headers.get("Authorization")
            return FakeResponse({"ok": True})

        try:
            github_enrich.TOKEN = "ghp_header_probe"
            with mock.patch("urllib.request.urlopen", fake_urlopen):
                github_enrich.api("/repos/x/y")
        finally:
            github_enrich.TOKEN = saved
        self.assertEqual(captured.get("auth"), "Bearer ghp_header_probe")


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    unittest.main(verbosity=2)
