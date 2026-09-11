#!/usr/bin/env python3
"""omni_delta.py — WP-P3 tier-1 delta review: scope, overlay, dispatch caps.

The delta review is a NORMAL omnireview run whose Phase 1 (omni_prepare.py)
carries --delta-files/--delta-base. Everything delta-specific that is pure
lives here:

- `load_delta_spec` — the dispatcher hands prepare a JSON delta spec;
  accepted shapes are a bare array of paths, {"files": [...]}, or the
  sweep-result envelope {"residual": {"files": [...]}} (whatever the seam
  has on disk).
- `apply_delta_overlay` — the anchoring-safe scoping (rev-2 tier 1): the
  full-MR partition stays the anchor truth — the overlay RESTRICTS
  deep-dive ownership to delta_files ∩ full-MR_files; the cross-cutting
  sweep and the files list stay the FULL MR set. NEVER a --since-sha
  gather: delta-relative line numbers silently mis-anchor threads.
- `delta_review_decision` — the dispatcher's caps: one delta review per
  push batch, a daily cap, never on non-ancestor deltas (ancestry is the
  dispatcher's job — consumed here, never computed), threshold consumed
  from the sweep result (computed by omni_sweep.residual_threshold with
  the per-project config), never recomputed.

Zero network, zero writes: the CLI prints ONE decision JSON line. The
engine owns the ledger (single-writer rule) — nothing here reads or writes
engine state; `reviewed_head := head` promotion happens in the engine's
run-completion write when the delta review (a normal review run) completes.
"""

import argparse
import json
import sys

DEFAULT_DAILY_CAP = 1


# ── delta spec loading ────────────────────────────────────────────────────

def load_delta_spec(path):
    """Read a delta spec file. Returns (files, reason): reason "ok" with a
    de-duplicated path list, or (None, reason) with reason in {"file not
    found", "unreadable", "invalid JSON", "unrecognized shape"} — the
    --prior-report vocabulary so callers treat both flags uniformly."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return None, "file not found"
    except OSError:
        return None, "unreadable"
    try:
        obj = json.loads(text)
    except ValueError:
        return None, "invalid JSON"

    files = None
    if isinstance(obj, list):
        files = obj
    elif isinstance(obj, dict):
        if isinstance(obj.get("files"), list):
            files = obj["files"]
        elif isinstance(obj.get("residual"), dict) \
                and isinstance(obj["residual"].get("files"), list):
            files = obj["residual"]["files"]
    if files is None or not all(isinstance(f, str) and f
                                for f in files):
        return None, "unrecognized shape"
    seen = []
    for f in files:
        if f not in seen:
            seen.append(f)
    return seen, "ok"


# ── anchoring-safe overlay ────────────────────────────────────────────────

def apply_delta_overlay(partition, delta_files):
    """Restrict deep-dive ownership to delta_files ∩ full-MR files.

    Pure: returns a NEW partition dict; the input is never mutated. The
    files list and every agent's cross_cutting_files stay the FULL MR set
    (the anchor truth — thread anchors come from the full-MR
    diff_line_map); only each agent's deep-dive owned list, its
    added_lines_total, and its weight_total shrink to the intersection.
    Files named in the delta but absent from the MR diff are silently
    dropped by the intersection itself (they have no anchors)."""
    delta_set = set(delta_files or [])
    added_by_path = {f["path"]: f["added_lines"] for f in partition["files"]}
    weight_by_path = {f["path"]: f.get("weight", 0)
                      for f in partition["files"]}
    agents = {}
    for agent, info in partition["agents"].items():
        scoped = [p for p in info["files"] if p in delta_set]
        agents[agent] = {
            "files": scoped,
            "added_lines_total": sum(added_by_path.get(p, 0)
                                     for p in scoped),
            "cross_cutting_files": list(info["cross_cutting_files"]),
            "weight_total": sum(weight_by_path.get(p, 0) for p in scoped),
        }
    return {
        "files": [dict(f) for f in partition["files"]],
        "agents": agents,
    }


def scoped_files(partition):
    """The union of agents' owned files after an overlay (the deep-dive
    scope), sorted for stable recording in prepare.json."""
    out = set()
    for info in partition["agents"].values():
        out.update(info["files"])
    return sorted(out)


# ── dispatch caps ─────────────────────────────────────────────────────────

def delta_review_decision(sweep_result, is_ancestor=True,
                          batch_delta_review_done=False,
                          delta_reviews_today=0,
                          daily_cap=DEFAULT_DAILY_CAP):
    """Should the dispatcher enqueue the ONE tier-1 delta review for this
    push batch? Guard order (first match wins, every refusal carries its
    reason): skipped sweep → non-ancestor delta → batch already reviewed →
    daily cap → threshold. The plugin never computes ancestry or counters
    — the dispatcher passes them; the threshold is CONSUMED from the sweep
    result's residual_decision (honoring whatever --threshold-config the
    sweep was invoked with), never recomputed."""
    result = sweep_result if isinstance(sweep_result, dict) else {}
    skip = result.get("skip")
    if skip:
        return {"dispatch": False,
                "reason": "sweep skipped: %s" % skip}
    if not is_ancestor:
        return {"dispatch": False,
                "reason": "non-ancestor delta (rebase/force-push): locus "
                          "unverifiable — full re-review recommended, "
                          "never a delta review"}
    if batch_delta_review_done:
        return {"dispatch": False,
                "reason": "batch already had its one delta review"}
    if delta_reviews_today >= daily_cap:
        return {"dispatch": False,
                "reason": "daily cap reached (%d/%d delta reviews today)"
                          % (delta_reviews_today, daily_cap)}
    decision = (result.get("residual_decision") or {})
    reason = decision.get("reason") or ""
    if decision.get("threshold_met"):
        return {"dispatch": True, "reason": reason or "residual threshold met"}
    return {"dispatch": False,
            "reason": "residual threshold not met%s"
                      % (": %s" % reason if reason else "")}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="WP-P3 tier-1 delta review dispatch decision (pure, "
                    "zero network): consumes a sweep result + the "
                    "dispatcher's ancestry/batch/daily facts, decides "
                    "whether the one delta review for this batch fires.")
    ap.add_argument("--sweep-result", required=True,
                    help="the sweep result JSON (omni_sweep.py stdout)")
    ap.add_argument("--no-ancestor", action="store_true",
                    help="the dispatcher's ancestry check failed "
                         "(rebase/force-push) — never dispatch")
    ap.add_argument("--batch-done", action="store_true",
                    help="this push batch already had its one delta review")
    ap.add_argument("--reviews-today", type=int, default=0,
                    help="today's delta-review count (the ledger's "
                         "counters.delta_reviews)")
    ap.add_argument("--daily-cap", type=int, default=DEFAULT_DAILY_CAP,
                    help="per-MR daily cap (default %d)"
                         % DEFAULT_DAILY_CAP)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    try:
        with open(args.sweep_result, "r", encoding="utf-8") as fh:
            sweep_result = json.load(fh)
    except OSError as e:
        print("omni_delta: cannot read sweep result: %s" % e,
              file=sys.stderr)
        return 2
    except ValueError as e:
        print("omni_delta: sweep result is not valid JSON: %s" % e,
              file=sys.stderr)
        return 2
    if not isinstance(sweep_result, dict):
        print("omni_delta: sweep result must be a JSON object",
              file=sys.stderr)
        return 2
    if args.daily_cap < 0 or args.reviews_today < 0:
        print("omni_delta: --daily-cap/--reviews-today must be >= 0",
              file=sys.stderr)
        return 2
    decision = delta_review_decision(
        sweep_result, is_ancestor=not args.no_ancestor,
        batch_delta_review_done=args.batch_done,
        delta_reviews_today=args.reviews_today,
        daily_cap=args.daily_cap)
    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
