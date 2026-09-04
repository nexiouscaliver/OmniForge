#!/usr/bin/env python3
"""omni_partition.py — deterministic COST-WEIGHTED load balancing for the 3
reviewer agents (R2-D). Every changed file gets exactly ONE deep-dive owner;
every agent still SEES every changed file (cross_cutting_files) — only the
deep-dive read depth is partitioned. The partition is a pure function of the
input JSON — same input, byte-identical output. Stdlib only. One JSON line on
stdout; diagnostics to stderr.

Weight model (spec section 4.1) — INTEGER arithmetic only, no floats anywhere:

    weight(path) = max(added_lines, 1) * LANG(path) * TEST(path)
    LANG = W_LANG_SECURITY (1000) if is_security(path) else W_LANG_DEFAULT (100)
    TEST = W_TEST_FILE    (50)   if is_test(path)     else W_TEST_DEFAULT (100)

The four constants are DOCUMENTED PRIORS, not telemetry-derived values
(spec deviation D5): the 12-run fleet telemetry contains NO per-file cost
attribution (per-subagent walls only), so no derivation is possible.
- Basis = added_lines (D1): the only per-file size datum the pipeline already
  carries; a byte-exact per-file diff parser was rejected (new binary/
  truncation/deletion-header edge surface, zero evidence bytes predict cost
  better than lines).
- W_LANG_SECURITY = 1000 (x10): the conservative end of the canary-implied
  band. window3-rollout.md records the !1402 dispatch walls 1084/1147/1152 s
  UNORDERED and UNATTRIBUTED next to the 110/109/9 file split; IF the
  shortest wall is attributed to the 9-file security agent, per-file cost is
  ~12x the mixed average (about 10.5-12.2x after subtracting a fixed
  overhead F in [0, 700] s). The only per-shard census (!1360,
  w3-tail-decomposition section 6) points the OPPOSITE way (codebase
  ~178-402 s/file vs security ~70 s/file) — the fragments are irreconcilable
  without per-file line counts that were never recorded. x10 is chosen
  because the cap bounds a wrong constant in BOTH directions: over-weight
  security -> no spill, ~= today's partition (safe default); under-weight ->
  spill bounded by the 1.25x cap.
- W_TEST_FILE = 50 (x0.5): an explicit HYPOTHESIS (root-plan section 7's
  cheap-balancing-currency directive — cheap test-heavy files sort last in
  largest-first greedy and act as fine-grained filler for the least-loaded
  bin); no per-file test-cost telemetry exists.
- Docs/code undifferentiated (LANG = 100 for both, open question OQ-2): no
  telemetry separates docs from code cost; the docs preference already
  routes the analyst lens.
- Retune ONLY on R2-E/fleet real-cost evidence — never on the section 8.3
  reviewer-sim walls (uniform-cost circularity). Constants are module ints,
  not CLI flags.

Balancing (spec section 5.1), integer cross-multiplication throughout:
- Pass 1: security affinity FIRST, always, regardless of cap — the section
  3.4 invariant: every is_security file is security-owned with reason
  security-affinity; affinity is NEVER spilled off security.
- Pass 2: docs/config prefer analyst over {analyst, codebase} only (analyst
  wins exact weight ties), cap-guarded.
- Pass 3: remainder greedy largest-first by WEIGHT (ties: path asc) into the
  least-loaded ELIGIBLE bin over ALL THREE agents — the one-way spill
  policy: security additionally RECEIVES greedy-balance filler when it is
  the least-loaded eligible bin, never past the cap; preference order
  codebase -> analyst -> security on exact ties.
- Cap eligibility: 12*load < 5*total (load < 1.25x the ideal share). Floor
  (outcome metric, not a separate mechanism): an agent is weight-starved
  iff 6*load < total (load < 0.5x ideal) — with all three agents greedy
  participants the least-loaded bin is lifted automatically. Scoped
  indivisibility bound (remainder pass): load <= 1.25x ideal + W_max —
  affinity preload is added verbatim and unbounded BY DESIGN, and the docs
  pass's indivisibility else-branch can stack a docs-heavy pair marginally
  past it (spec section 4.2); neither is ever a failure exit.
- Emission stays in the UNCHANGED canonical order sorted by
  (-added_lines, path), so output bytes never depend on the input list
  order; only the greedy ITERATION is by (-weight, path).

Reason vocabulary stays exactly {security-affinity, docs-prefer-analyst,
greedy-balance}. Output schema is additive: files[] gains weight (int) and
agents.<owner> gains weight_total (int); added_lines_total keeps its exact
pre-R2-D meaning (sum of owned files' added_lines — it is NOT replaced by
weight)."""

import argparse
import json
import os
import re
import sys

AGENTS = ("analyst", "codebase", "security")

SECURITY_TOKENS = ("auth", "login", "session", "token", "secret", "password",
                   "crypto", "jwt", "permission")
SECURITY_PATHS = (".gitlab-ci.yml", ".github/workflows/", "dockerfile",
                  "docker-compose", ".env")
SECURITY_SUFFIXES = (".sql",)          # migrations/SQL
DOCS_SUFFIXES = (".md", ".txt", ".license")
DOCS_NAMES = ("license", "notice")

# R2-D cost-model constants (spec section 4.1) — documented priors, see the
# module docstring for the full derivation chain and retune policy.
W_LANG_DEFAULT = 100
W_LANG_SECURITY = 1000
W_TEST_DEFAULT = 100
W_TEST_FILE = 50
TEST_SEGMENTS = ("test", "tests", "spec", "specs")
TEST_BASE_PREFIXES = ("test_",)
TEST_BASE_SUFFIXES = ("_test.py", "_test.go", "_test.js", "_test.rb",
                      ".spec.js", ".spec.ts", "_spec.rb")


def is_security(path):
    p = path.lower()
    # tokens match path SEGMENTS, never substrings: "auth" must not fire on
    # author/AUTHORS, "token" not on tokenizer (underscore-compound names
    # like auth_middleware.py still match via the segment split)
    segments = set(re.split(r"[/_.-]", p))
    return (bool(segments & set(SECURITY_TOKENS))
            or any(s in p for s in SECURITY_PATHS)
            or p.endswith(SECURITY_SUFFIXES))


def is_docs(path):
    p = path.lower()
    return p.endswith(DOCS_SUFFIXES) or os.path.basename(p) in DOCS_NAMES \
        or (p.endswith(".json") and "config" in p)


def added_lines(mr, path):
    entry = (mr.get("diff_line_map") or {}).get(path)
    if isinstance(entry, dict):
        return len(entry.get("added_lines") or [])
    if isinstance(entry, int):
        return entry
    return 0


def is_test(path):
    """Path SEGMENTS (same split style as is_security — substring hits like
    latest.py/contest.py must not fire; __tests__ splits to segment `tests`
    and matches), OR basename starts with test_, OR basename ends with a
    pinned suffix."""
    p = path.lower()
    segments = set(re.split(r"[/_.-]", p))
    base = p.rsplit("/", 1)[-1]
    return (bool(segments & set(TEST_SEGMENTS))
            or base.startswith(TEST_BASE_PREFIXES)
            or p.endswith(TEST_BASE_SUFFIXES))


def weight(path, added):
    """Per-file review-cost weight: INTEGER only (spec section 4.1)."""
    lang = W_LANG_SECURITY if is_security(path) else W_LANG_DEFAULT
    test = W_TEST_FILE if is_test(path) else W_TEST_DEFAULT
    return max(added, 1) * lang * test


def partition(mr):
    """Pure function: saved fetch_mr_data JSON (dict) -> partition dict.

    Passes: (1) security affinity FIRST, always, regardless of cap (the
    section 3.4 invariant); (2) docs preference over {analyst, codebase}
    only, cap-guarded (analyst wins exact weight ties); (3) remainder greedy
    largest-first by WEIGHT (ties: path asc) into the least-loaded ELIGIBLE
    bin over ALL THREE agents, preference order codebase -> analyst ->
    security on exact ties. Cap eligibility: 12*load < 5*total (load <
    1.25x ideal) — exact-integer cross-multiplication, no division. Emission
    stays in the UNCHANGED canonical order sorted by (-added_lines, path),
    so output bytes never depend on input list order.
    """
    files_changed = [f for f in (mr.get("files_changed") or [])
                     if isinstance(f, str)]
    added = {p: added_lines(mr, p) for p in files_changed}
    w = {p: weight(p, added[p]) for p in files_changed}
    total = sum(w.values())
    owned = {}
    load = {a: 0 for a in AGENTS}

    def eligible(a):
        return 12 * load[a] < 5 * total

    def wkey(p):
        return (-w[p], p)

    # (1) security affinity FIRST — hard invariant; never spill OFF security
    for path in sorted((f for f in files_changed if is_security(f)),
                       key=wkey):
        owned[path] = ("security", "security-affinity")
        load["security"] += w[path]

    # (2) docs/config prefer analyst (analyst wins exact ties), cap-guarded
    for path in sorted((f for f in files_changed
                        if f not in owned and is_docs(f)), key=wkey):
        if load["analyst"] <= load["codebase"] and eligible("analyst"):
            owner, reason = "analyst", "docs-prefer-analyst"
        elif eligible("codebase"):
            owner, reason = "codebase", "greedy-balance"
        elif eligible("analyst"):
            owner, reason = "analyst", "greedy-balance"
        else:                      # indivisibility: least-loaded of the two
            owner = "analyst" if load["analyst"] <= load["codebase"] \
                else "codebase"
            reason = "greedy-balance"
        owned[path] = (owner, reason)
        load[owner] += w[path]

    # (3) remainder greedy over ALL THREE bins (the spill rule)
    PREF = ("codebase", "analyst", "security")
    for path in sorted((f for f in files_changed if f not in owned),
                       key=wkey):
        cands = [a for a in PREF if eligible(a)]
        if not cands:              # indivisibility: least-loaded anyway
            cands = [min(PREF, key=lambda a: (load[a], PREF.index(a)))]
        owner = min(cands, key=lambda a: (load[a], PREF.index(a)))
        owned[path] = (owner, "greedy-balance")
        load[owner] += w[path]

    def sort_key(path):            # canonical emission order — UNCHANGED
        return (-added[path], path)

    ordered = sorted(files_changed, key=sort_key)
    files_out = [{"path": p, "added_lines": added[p],
                  "owner": owned[p][0], "reason": owned[p][1],
                  "weight": w[p]} for p in ordered]
    agents_out = {a: {"files": [p for p in ordered if owned[p][0] == a],
                      "added_lines_total": sum(added[p] for p in ordered
                                               if owned[p][0] == a),
                      "cross_cutting_files": list(ordered),
                      "weight_total": load[a]} for a in AGENTS}
    return {"files": files_out, "agents": agents_out}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mr-json", required=True,
                    help="saved Phase-1 fetch_mr_data JSON (files_changed + diff_line_map)")
    ap.add_argument("--out", required=True, help="partition.json output path")
    a = ap.parse_args(argv)
    try:
        with open(a.mr_json, "r", encoding="utf-8") as fh:
            mr = json.load(fh)
    except OSError as e:
        print(f"omni_partition: cannot read mr-json: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"omni_partition: mr-json is not valid JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(mr, dict):
        print("omni_partition: mr-json must be a JSON object", file=sys.stderr)
        return 2
    if isinstance(mr.get("data"), dict) and isinstance(mr.get("discussions"), (dict, list)):
        mr = mr["data"]          # gather-file form: operate on the embedded envelope
    result = partition(mr)
    try:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as e:
        print(f"omni_partition: cannot write out: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"files": len(result["files"]),
                      "agents": {ag: len(result["agents"][ag]["files"])
                                 for ag in AGENTS}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
