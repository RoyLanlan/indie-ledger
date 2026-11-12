#!/usr/bin/env python3
"""Path resolver shared by every script in this project.

The scripts exist in two layouts and BOTH must work:

  working copy : <root>/pipeline.py        with <root>/data, <root>/reports
  published    : <root>/src/pipeline.py   with <root>/data, <root>/reports

Resolving data/ relative to the script's own directory silently breaks the
published layout: `src/data/ledger.jsonl` does not exist, so every default
path points at a file that is not there and the script reports "0 rows"
instead of failing loudly.

Rule: the directory that CONTAINS data/ (or reports/) is the root. Check
the script's own directory first, then its parent.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def repo_root(start=None):
    start = start or HERE
    for cand in (start, os.path.dirname(start)):
        if os.path.isdir(os.path.join(cand, "data")) or \
           os.path.isdir(os.path.join(cand, "reports")):
            return cand
    return start


ROOT = repo_root()
DATA = os.path.join(ROOT, "data")
REPORTS = os.path.join(ROOT, "reports")
