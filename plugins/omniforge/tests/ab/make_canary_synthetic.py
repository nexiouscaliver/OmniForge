#!/usr/bin/env python3
"""Synthetic canary-shape gather generator (R2-D spec section 8.1).

Seeds are FIXED: canary 1402 (110 code-ish / 109 test-ish / 9 security-ish
files), security-light 1403 (80 / 40 / 2; the 2 security files are tiny,
<= 5 added lines — the deterministic shape where old-vs-new shows the
designed floor lift). Synthetic assumption, documented: per-file
added_lines drawn log-uniformly, code 5-200; tests drawn 1.5x the code
distribution (NO telemetry per-file line counts exist — w3-tail-
decomposition section 6 has only per-shard averages). Output = a full
omniforge-mr-gather/1 envelope (the partitioner's --mr-json unwrap rule
needs data + discussions at top level). Deterministic: same profile ->
byte-identical output. main() self-checks counts + classifier agreement
and exits 2 on drift (the committed fixtures are trusted shape controls).
"""
import argparse
import importlib.util
import json
import math
import os
import random
import sys

# importlib-loads the sibling partition script for is_security/is_test —
# the self-check asserts every security-ish path matches is_security, every
# test-ish path matches is_test, and NO code-ish path matches either.

PROFILE_SEEDS = {"canary": 1402, "security-light": 1403}
SHAPES = {"canary": (110, 109, 9), "security-light": (80, 40, 2)}

PARTITION_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "skills", "omnireview-gitlab",
                                "scripts", "omni_partition.py")


def _lines(rng, lo, hi):
    return max(1, int(round(math.exp(rng.uniform(math.log(lo),
                                                 math.log(hi))))))


def build_gather(profile):
    n_code, n_test, n_sec = SHAPES[profile]
    rng = random.Random(PROFILE_SEEDS[profile])
    files = {}
    for i in range(n_code):
        files["src/mod%03d/service%03d.py" % (i, i)] = _lines(rng, 5, 200)
    for i in range(n_test):
        files["tests/test_mod%03d.py" % i] = _lines(rng, 8, 300)  # ~1.5x code
    hi = 5 if profile == "security-light" else 200
    for i in range(n_sec):
        files["src/auth/handler%02d.py" % i] = _lines(rng, 1, hi)
    return {
        "schema": "omniforge-mr-gather/1",
        "fetched_at": "2026-09-05T00:00:00+00:00",
        "project": "73281071", "mr_iid": "1402",
        "diff_refs": {"base_sha": "b", "head_sha": "h", "start_sha": "s"},
        "data": {
            "title": "synthetic %s canary" % profile,
            "files_changed": sorted(files),
            "diff_line_map": {p: {"added_lines": list(range(1, n + 1))}
                              for p, n in sorted(files.items())},
        },
        "discussions": {"success": True, "mr_id": "1402",
                        "discussions": [], "total": 0,
                        "unresolved": 0, "resolved": 0},
        "versions": [],
    }


def _load_partition():
    spec = importlib.util.spec_from_file_location(
        "omni_partition_shape_check", PARTITION_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def self_check(gather, profile):
    """Counts + classifier agreement against the sibling partition script.

    Returns a list of drift problems (empty = pass). is_test is absent from
    the in-tree partitioner ONLY in the pre-T1-green transient state — in
    that state the test-classifier half is skipped with a stderr notice (the
    fixture bytes never depend on it; build_gather is module-free); from
    T1-green onward the full agreement check runs and any drift exits 2.
    """
    problems = []
    mod = _load_partition()
    is_test_fn = getattr(mod, "is_test", None)
    if is_test_fn is None:
        print("make_canary_synthetic: in-tree omni_partition.py lacks "
              "is_test (pre-T1-green state) — test-classifier agreement "
              "check skipped", file=sys.stderr)
    paths = gather["data"]["files_changed"]
    code = [p for p in paths if p.startswith("src/mod")]
    tests = [p for p in paths if p.startswith("tests/")]
    sec = [p for p in paths if p.startswith("src/auth/")]
    counts = (len(code), len(tests), len(sec))
    if counts != SHAPES[profile]:
        problems.append("counts %r != expected %r" % (counts,
                                                      SHAPES[profile]))
    for p in sec:
        if not mod.is_security(p):
            problems.append("security-ish path not is_security: %s" % p)
    for p in code:
        if mod.is_security(p):
            problems.append("code-ish path is_security: %s" % p)
        if is_test_fn is not None and is_test_fn(p):
            problems.append("code-ish path is_test: %s" % p)
    if is_test_fn is not None:
        for p in tests:
            if not is_test_fn(p):
                problems.append("test-ish path not is_test: %s" % p)
            if mod.is_security(p):
                problems.append("test-ish path is_security: %s" % p)
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate a deterministic synthetic canary-shape "
                    "omniforge-mr-gather/1 fixture (fixed seeds)")
    ap.add_argument("--profile", choices=sorted(PROFILE_SEEDS),
                    default="canary",
                    help="shape profile (default: canary)")
    ap.add_argument("--out", default=None,
                    help="output file path (default: stdout)")
    a = ap.parse_args(argv)
    gather = build_gather(a.profile)
    problems = self_check(gather, a.profile)
    if problems:
        for p in problems:
            print("make_canary_synthetic: drift: %s" % p, file=sys.stderr)
        return 2
    text = json.dumps(gather, indent=2, ensure_ascii=False) + "\n"
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
