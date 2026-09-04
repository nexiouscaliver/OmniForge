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


# ── fetch-child classification ───────────────────────────────────────────

def _error_from_stdout(text):
    """The fetch child's exit-1 stdout JSON line's error string ("" if the
    stdout is not a JSON object with a string error)."""
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("error"), str):
            return obj["error"]
    return ""


def classify_fetch(rc, stdout_text):
    """Map the fetch child's (returncode, stdout) to ok | auth | gather_fail.

    rc 0 -> ok. rc 1: parse the stdout JSON error and extract the HTTP
    status as an INTEGER (re.search(r"failed with HTTP (\\d+)")); 401/403
    -> auth. Everything else — including rc 2 (non-token preconditions) —
    is gather_fail. Substring matching is forbidden: the message format
    puts the true status FIRST, so a 5xx whose detail body contains the
    literal "HTTP 403" extracts 503 and classifies as gather_fail.
    """
    if rc == 0:
        return "ok"
    if rc == 1:
        m = re.search(r"failed with HTTP (\d+)",
                      _error_from_stdout(stdout_text))
        if m and int(m.group(1)) in (401, 403):
            return "auth"
    return "gather_fail"


def _short(text):
    """One short single-line detail string for stdout/stderr diagnostics."""
    t = (text or "").strip()
    if not t:
        return ""
    return t.splitlines()[0][:_DETAIL_CAP]


# ── gather consumption ───────────────────────────────────────────────────

def load_gather(path):
    """Load gather.json (the fetch child's omniforge-mr-gather/1 output,
    consumed as-is). Raises ValueError on an unexpected shape or parse
    error — main maps that to exit 1, stage "internal"."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            gather = json.load(fh)
    except OSError as e:
        raise ValueError("cannot read gather: %s" % e)
    except ValueError as e:
        raise ValueError("gather is not valid JSON: %s" % e)
    if not (isinstance(gather, dict) and isinstance(gather.get("data"), dict)
            and isinstance(gather.get("fetched_at"), str)):
        raise ValueError(
            "unexpected gather shape: expected omniforge-mr-gather/1 with "
            "fetched_at + data")
    return gather


def _fail_stage(stage, detail):
    """The exit-1 contract: one stdout JSON line + one stderr line."""
    print(json.dumps({"ok": False, "error": "prepare_failed",
                      "stage": stage, "detail": _short(detail)}))
    print("omni_prepare: %s stage failed: %s"
          % (stage, _short(detail)), file=sys.stderr)
    return 1


# ── partition subprocess ──────────────────────────────────────────────────

def run_partition(gather_path, partition_path):
    """Run the REAL omni_partition.py subprocess over the gathered JSON (it
    reads the gather-file shape natively — embedded data envelope). Returns
    (returncode, stdout); diagnostics stay on the child's stderr."""
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "omni_partition.py"),
         "--mr-json", gather_path, "--out", partition_path],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout


# ── brief renderer ────────────────────────────────────────────────────────

# Empty-owned marker (T1 scope): files_changed == [] means an EMPTY DIFF.
# (The deleted-files-only wording variant keys on a non-blank diff and is
# deliberately NOT implemented at this tip.)
_EMPTY_DIFF_MARKER = "(none — this MR has an empty diff)"

_DEPTH_SENTENCES = (
    "Cross-cutting: you still sweep ALL changed files at grep depth; "
    "full-file reads are your\nowned files only.")

_DISPATCH_NOTE = (
    "The orchestrator fills `{OWNED_FILES}` in the reference template "
    "with this brief's\n\"Owned files\" section above (owned list + both "
    "depth sentences) — nothing else from this\nfile. Worktree path is "
    "assigned at dispatch (Phase 2).")


def render_brief(agent, gather, partition, prior_count):
    """Render one reviewer brief (section 2c template, byte-pinned by the
    hand-authored golden fixtures — the renderer was built to match THEM).

    Deterministic: no wall-clock/uuid/random; the only timestamp is
    gather["fetched_at"], verbatim. No per-file diff content is ever
    rendered (binary-safe). `gather` carries the run's review_id (main()
    injects args.review_id before rendering).
    """
    data = gather.get("data") or {}
    head_sha = (gather.get("diff_refs") or {}).get("head_sha") or ""
    files = partition.get("files") or []
    files_total = len(files)
    capped = files_total > BRIEF_FILE_CAP
    added_by_path = {f["path"]: f["added_lines"] for f in files}
    reason_by_path = {f["path"]: f["reason"] for f in files}
    agent_info = (partition.get("agents") or {}).get(agent) or {}
    owned = list(agent_info.get("files") or [])
    owned_added = agent_info.get("added_lines_total") or 0
    added_total = sum(f["added_lines"] for f in files)
    empty_mr = (data.get("files_changed") or []) == []

    lines = [
        "# OmniForge reviewer brief — %s (agent-%d)"
        % (AGENT_TITLES[agent], 1 + AGENTS.index(agent)),
        "",
        "- Review ID: %s" % gather.get("review_id", ""),
        "- Project: %s" % gather.get("project", ""),
        "- MR: !%s — %s" % (gather.get("mr_iid", ""), data.get("title", "")),
        "- Branches: %s → %s" % (data.get("source_branch", ""),
                                 data.get("target_branch", "")),
        "- Head SHA: %s" % head_sha,
        "- Generated: %s" % gather.get("fetched_at", ""),
        "",
        "## Owned files (deep-dive ownership)",
        "",
        "Deep-dive owner: these files:",
        "",
    ]
    if empty_mr:
        lines.append(_EMPTY_DIFF_MARKER)
    elif owned:
        for path in owned:
            if capped:
                # cap: owned entries drop the reason; full detail stays in
                # partition.json
                lines.append("- `%s` — %d added lines"
                             % (path, added_by_path.get(path, 0)))
            else:
                lines.append("- `%s` — %d added lines — %s"
                             % (path, added_by_path.get(path, 0),
                                reason_by_path.get(path, "")))
    # else: a non-empty MR where this agent owns nothing — no owned bullets
    # (the cross-cutting sweep below is still this agent's job)
    lines += ["", _DEPTH_SENTENCES, "",
              "## Cross-cutting files (all %d changed files)" % files_total,
              ""]
    if capped:
        lines.append("Cross-cutting: all %d changed files (see "
                     "partition.json)" % files_total)
    elif empty_mr:
        lines.append(_EMPTY_DIFF_MARKER)
    else:
        lines.extend("- `%s`" % f["path"] for f in files)
    lines += [
        "",
        "## Stats",
        "",
        "- Owned: %d files / %d added lines" % (len(owned), owned_added),
        "- Cross-cutting: %d files" % files_total,
        "- MR total: %d files / %d added lines" % (files_total, added_total),
    ]
    if prior_count is not None:
        lines.append("- Prior review findings: %d — see prior report"
                     % prior_count)
    lines += ["", "## Dispatch note", "", _DISPATCH_NOTE]
    return "\n".join(lines) + "\n"


def build_prepare_json(review_id, project, mr_iid, head_sha, run_dir,
                       partition, prior_report, elapsed_ms,
                       created_at=None):
    """The 15-key prepare.json dict (exact key set, section 2c). Pure:
    created_at defaults to wall-clock NOW but is injectable so tests can
    normalize it."""
    run_dir = os.path.abspath(run_dir)
    paths = output_paths(run_dir)
    if created_at is None:
        created_at = datetime.datetime.now(
            datetime.timezone.utc).isoformat()
    return {
        "schema": SCHEMA,
        "created_at": created_at,
        "review_id": review_id,
        "project": project,
        "mr_iid": str(mr_iid),
        "head_sha": head_sha,
        "run_dir": run_dir,
        "gather_json": paths["gather_json"],
        "partition_json": paths["partition_json"],
        "briefs": {a: os.path.join(run_dir, "briefs", AGENT_FILES[a])
                   for a in AGENTS},
        "files": len(partition["files"]),
        "added_lines": sum(f["added_lines"] for f in partition["files"]),
        "partitions": {a: {"files": len(partition["agents"][a]["files"]),
                           "added_lines_total":
                               partition["agents"][a]["added_lines_total"]}
                       for a in AGENTS},
        "prior_report": prior_report,
        "elapsed_ms": int(elapsed_ms),
    }


def build_receipt(run_dir, project, mr_iid, review_id, head_sha, partition,
                  prior_count, duration_s):
    """The exit-0 stdout receipt (exactly ONE JSON line, 11 keys)."""
    return {
        "ok": True,
        "run_dir": os.path.abspath(run_dir),
        "project": project,
        "mr_iid": str(mr_iid),
        "review_id": review_id,
        "head_sha": head_sha,
        "files": len(partition["files"]),
        "added_lines": sum(f["added_lines"] for f in partition["files"]),
        "partitions": {a: len(partition["agents"][a]["files"])
                       for a in AGENTS},
        "prior_findings": prior_count,
        "duration_s": float(duration_s),
    }


def build_phases_line(review_id, duration_s, files, partitions, head_sha,
                      ts=None):
    """One phases.jsonl journal entry (7 keys). ts defaults to wall-clock
    NOW but is injectable. Appended ONLY after every other output landed."""
    return {
        "phase": "prepare",
        "review_id": review_id,
        "duration_s": float(duration_s),
        "files": files,
        "partitions": partitions,
        "head_sha": head_sha,
        "ts": ts if ts is not None else datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
    }


# ── writers ───────────────────────────────────────────────────────────────

def write_atomic(path, text):
    """Write text to path via <path>.tmp + os.replace (R6 atomicity).
    Raises OSError; the .tmp sibling lives in the same directory."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _cannot_write(path, e):
    """The R6 exit-2 diagnostic for OSError writing/wiping outputs."""
    print("omni_prepare: cannot write %s: %s" % (path, e), file=sys.stderr)
    return 2


def _wipe_run_dir(run_dir):
    """R1: remove every entry inside run_dir EXCEPT phases.jsonl (the
    exemption is BY NAME — files, dirs, and symlinks alike), so every
    successful run starts fresh while the append journal survives. Runs
    after preconditions, before the fetch: a failing invocation never
    destroys a prior run's artifacts."""
    for name in os.listdir(run_dir):
        if name == PHASES_NAME:
            continue
        path = os.path.join(run_dir, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)


# ── entry point ──────────────────────────────────────────────────────────

def main(argv=None):
    args = parse_args(argv)

    if args.dry_run:
        print(json.dumps(dry_run_plan(args)))
        return 0

    started = time.monotonic()
    run_dir = os.path.abspath(args.run_dir)
    try:
        os.makedirs(run_dir, exist_ok=True)
    except OSError as e:
        print("omni_prepare: cannot create run dir: %s" % e,
              file=sys.stderr)
        return 2

    if not omni_glab_api.resolve_token():
        print(json.dumps({"ok": False, "error": "auth",
                          "detail": "GITLAB_TOKEN not set"}))
        print(omni_fetch_mr.token_fix_line(omni_glab_api.resolve_host(None)),
              file=sys.stderr)
        return 3

    # R1 wipe: after preconditions, before the fetch (a failing exit-2/3
    # invocation must not destroy a prior run's artifacts)
    try:
        _wipe_run_dir(run_dir)
    except OSError as e:
        return _cannot_write(run_dir, e)

    paths = output_paths(run_dir)
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "omni_fetch_mr.py"),
         "--project", args.project, "--mr", args.iid,
         "--out", paths["gather_json"]],
        capture_output=True, text=True)
    verdict = classify_fetch(proc.returncode, proc.stdout)
    if verdict == "auth":
        detail = _short(_error_from_stdout(proc.stdout)) or "auth failure"
        print(json.dumps({"ok": False, "error": "auth", "detail": detail}))
        print(omni_fetch_mr.token_fix_line(omni_glab_api.resolve_host(None)),
              file=sys.stderr)
        return 3
    if verdict != "ok":
        detail = _error_from_stdout(proc.stdout) or proc.stderr \
            or "fetch rc=%d" % proc.returncode
        return _fail_stage("gather", detail)

    try:
        gather = load_gather(paths["gather_json"])
    except ValueError as e:
        return _fail_stage("internal", e)

    head_sha = (gather.get("diff_refs") or {}).get("head_sha") or ""
    if args.verify_head is not None and args.verify_head != head_sha:
        print(json.dumps({"ok": False, "head_moved": True,
                          "recorded_head": args.verify_head,
                          "current_head": head_sha}))
        print("omni_prepare: head moved: --verify-head %s but gathered "
              "head is %s" % (args.verify_head, head_sha), file=sys.stderr)
        return 4

    part_rc, part_stdout = run_partition(paths["gather_json"],
                                         paths["partition_json"])
    if part_rc != 0:
        return _fail_stage("partition",
                           _error_from_stdout(part_stdout)
                           or "partition rc=%d" % part_rc)

    try:
        with open(paths["partition_json"], encoding="utf-8") as fh:
            partition = json.load(fh)
    except (OSError, ValueError) as e:
        return _fail_stage("internal", "cannot read partition: %s" % e)

    gather["review_id"] = args.review_id      # renderer injection
    elapsed_ms = int((time.monotonic() - started) * 1000)
    prepare_doc = build_prepare_json(
        review_id=args.review_id, project=args.project, mr_iid=args.iid,
        head_sha=head_sha, run_dir=run_dir, partition=partition,
        prior_report=None, elapsed_ms=elapsed_ms)
    outputs = [(os.path.join(run_dir, "briefs", AGENT_FILES[a]),
                render_brief(a, gather, partition, None)) for a in AGENTS]
    outputs.append((paths["prepare_json"],
                    json.dumps(prepare_doc, indent=2, ensure_ascii=False)
                    + "\n"))
    writing = os.path.join(run_dir, "briefs")
    try:
        os.makedirs(writing, exist_ok=True)
        for path, text in outputs:
            writing = path
            write_atomic(path, text)
    except OSError as e:
        return _cannot_write(writing, e)

    duration_s = time.monotonic() - started
    part_counts = {a: len(partition["agents"][a]["files"]) for a in AGENTS}
    try:
        # R6: the journal append is the LAST writer — a run is recorded
        # only after every other output landed
        with open(paths["phases_jsonl"], "a", encoding="utf-8") as fh:
            fh.write(json.dumps(build_phases_line(
                args.review_id, duration_s, prepare_doc["files"],
                part_counts, head_sha), ensure_ascii=False) + "\n")
    except OSError as e:
        return _cannot_write(paths["phases_jsonl"], e)

    print(json.dumps(build_receipt(
        run_dir=run_dir, project=args.project, mr_iid=args.iid,
        review_id=args.review_id, head_sha=head_sha, partition=partition,
        prior_count=None, duration_s=duration_s), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
