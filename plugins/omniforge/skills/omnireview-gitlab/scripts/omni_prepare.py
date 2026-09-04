#!/usr/bin/env python3
"""omni_prepare.py — deterministic Phase-1 pre-dispatch (round2-pre-dispatch).

ONE invocation collapses the improvised Phase 1 into a script: it runs
omni_fetch_mr.py as a subprocess (the single 6-GET HTTP pass), then
omni_partition.py as a subprocess, renders the three reviewer briefs, and
writes prepare.json + appends the phases.jsonl journal into the run dir.
Stdlib only; sibling scripts resolve relative to this file, never CWD.

CLI:
  python3 omni_prepare.py --project <id-or-fullpath> --iid <n> \
      --review-id <id> --run-dir <dir> \
      [--prior-report <path>] [--verify-head <sha>] [--dry-run]

--iid maps to the fetcher's --mr. --dry-run validates the invocation only:
ONE plan JSON line, no network, no mkdir, no writes, no token; a bad
--prior-report is REPORTED (exists/valid_json), never fatal.

Exit codes:
  0   all outputs written; stdout = ONE receipt JSON line (11 keys).
  1   gather/partition/internal failure:
      {"ok":false,"error":"prepare_failed","stage":"gather"|"partition"|
       "internal","detail":<short str>} + one stderr diagnostic line.
  2   argparse usage (natural); unwritable run-dir or OSError
      writing/wiping outputs (no stdout JSON).
  3   token missing at start, or the fetch child exited 1 with integer
      HTTP 401/403 (extracted from its error message, never a substring
      match): {"ok":false,"error":"auth","detail":<short>} + the glab
      extraction one-liner (omni_fetch_mr.token_fix_line) on stderr.
  4   --verify-head given and the gathered head differs (compared AFTER
      the full gather): {"ok":false,"head_moved":true,"recorded_head":...,
      "current_head":...} — STOP before partition/briefs/prepare.json.

Run-dir outputs: gather.json, partition.json, prepare.json (atomic
.tmp + os.replace), briefs/agent-1..3.md, phases.jsonl (append; written
LAST — a run is journaled only after every other output landed). The
run dir is wiped of stale artifacts on every real run EXCEPT phases.jsonl
(the append journal survives; the wipe runs after preconditions so a
failing invocation never destroys a prior run's outputs).

Determinism: briefs are byte-identical given identical gather.json — the
only timestamp in a brief is gather.fetched_at, verbatim; renderers call
no wall-clock/uuid/random.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import omni_fetch_mr          # noqa: E402  (sibling, never CWD-relative)
import omni_glab_api          # noqa: E402

SCHEMA = "omniforge-prepare/1"
AGENTS = ("analyst", "codebase", "security")
AGENT_FILES = {"analyst": "agent-1.md", "codebase": "agent-2.md",
               "security": "agent-3.md"}
AGENT_TITLES = {"analyst": "MR Analyst", "codebase": "Codebase Reviewer",
                "security": "Security Reviewer"}
BRIEF_FILE_CAP = 500
PHASES_NAME = "phases.jsonl"

_DETAIL_CAP = 300             # short detail strings stay one line


# ── CLI ──────────────────────────────────────────────────────────────────

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic Phase-1 pre-dispatch: one gather pass, "
                    "partition, reviewer briefs, prepare.json, phases line.")
    ap.add_argument("--project", required=True,
                    help="project ID, pre-encoded URL path, or bare full "
                         "path (group/subgroup/project)")
    ap.add_argument("--iid", required=True, help="merge request IID")
    ap.add_argument("--review-id", required=True,
                    help="free-form review identifier")
    ap.add_argument("--run-dir", required=True,
                    help="review run directory (wiped of stale artifacts "
                         "except phases.jsonl on every real run)")
    ap.add_argument("--prior-report", default=None,
                    help="prior findings JSON (array, or object with a "
                         "\"findings\" array)")
    ap.add_argument("--verify-head", default=None, metavar="SHA",
                    help="compare gather.diff_refs.head_sha AFTER the full "
                         "gather; mismatch -> exit 4")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate the invocation only: one plan JSON "
                         "line, no network, no writes, no token")
    return ap.parse_args(argv)


def output_paths(run_dir):
    """Absolute output paths inside the run dir (briefs order = agents)."""
    briefs_dir = os.path.join(run_dir, "briefs")
    return {
        "gather_json": os.path.join(run_dir, "gather.json"),
        "partition_json": os.path.join(run_dir, "partition.json"),
        "prepare_json": os.path.join(run_dir, "prepare.json"),
        "briefs": [os.path.join(briefs_dir, AGENT_FILES[a]) for a in AGENTS],
        "phases_jsonl": os.path.join(run_dir, PHASES_NAME),
    }


# ── prior-report seam ────────────────────────────────────────────────────

def load_prior_report(path):
    """Recognized shapes: top-level JSON array, or object with a "findings"
    array. Returns ("ok", findings_count) or (reason, -1) with reason in
    {"file not found", "unreadable", "invalid JSON", "unrecognized
    shape"}."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return ("file not found", -1)
    except OSError:
        return ("unreadable", -1)
    try:
        obj = json.loads(text)
    except ValueError:
        return ("invalid JSON", -1)
    if isinstance(obj, list):
        return ("ok", len(obj))
    if isinstance(obj, dict) and isinstance(obj.get("findings"), list):
        return ("ok", len(obj["findings"]))
    return ("unrecognized shape", -1)


def _prior_report_stat(path):
    """Dry-run stat: report the file, never judge the caller."""
    stat = {"path": path, "exists": os.path.exists(path),
            "valid_json": False, "findings_count": None}
    if stat["exists"]:
        reason, count = load_prior_report(path)
        if reason == "ok":
            stat["valid_json"] = True
            stat["findings_count"] = count
        elif reason == "unrecognized shape":
            stat["valid_json"] = True
    return stat


# ── dry-run plan ─────────────────────────────────────────────────────────

def dry_run_plan(args):
    """The --dry-run plan line (key set exact; never the token value)."""
    run_dir = os.path.abspath(args.run_dir)
    plan = {
        "ok": True,
        "dry_run": True,
        "run_dir": run_dir,
        "project": args.project,
        "mr_iid": str(args.iid),
        "review_id": args.review_id,
        "outputs": output_paths(run_dir),
        "prior_report": None,
        "token_present": bool(omni_glab_api.resolve_token()),
        "verify_head": args.verify_head,
    }
    if args.prior_report:
        plan["prior_report"] = _prior_report_stat(args.prior_report)
    return plan


# ── entry point ──────────────────────────────────────────────────────────

def main(argv=None):
    args = parse_args(argv)

    if args.dry_run:
        print(json.dumps(dry_run_plan(args)))
        return 0

    os.makedirs(os.path.abspath(args.run_dir), exist_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
