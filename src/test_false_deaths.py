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
        self.assertEqual(len(seen), 1, "451 must not be retried as rate limit")

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
            {"launch_day": "2024-01-01", "verdict": "blocked"},
            {"launch_day": "2024-01-01", "verdict": "unavailable"},
            {"launch_day": "2024-01-01", "verdict": "active"},
        ]
        row = cohort_table(products)[0]
        self.assertEqual(row["dead"], 0)
        self.assertEqual(row["unavailable"], 2)   # blocked + unavailable
        self.assertEqual(row["verified"], 1)      # only `active` counts


class TestVerdictSemantics(unittest.TestCase):
    def test_none_is_dead(self):
        ev = classify_repo(None, "2024-01-01")
        self.assertEqual(ev["verdict"], "dead")
        self.assertIn("404", ev["reason"])
        self.assertIsNotNone(ev["survival_days"],
                            "dead rows must still carry survival_days")

    def test_archived_is_dead(self):
        ev = classify_repo({"archived": True, "pushed_at": "2020-01-01T00:00:00Z",
                            "created_at": "2019-01-01T00:00:00Z"}, "2024-01-01")
        self.assertEqual(ev["verdict"], "dead")
        self.assertEqual(ev["reason"], "archived")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
