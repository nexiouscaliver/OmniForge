#!/usr/bin/env python3
"""omni_post_review.py — MCP-free GitLab MR review posting fallback (omnireview-gitlab).

MCP-PRIMARY NOTE: when the MCP server is available, posting runs through
mcp__omniforge__post_full_review (single-call, N+1-safe — the b5efc0b fix in
tools/omniforge_mcp_server.py), and open-prior replies run through
mcp__omniforge__reply_to_discussion. This script is the FALLBACK for runs
where the MCP server cannot start (e.g. mcp 2.x renamed FastMCP, so the
server import fails on the box): it is STANDALONE — stdlib only, imports
nothing from omniforge_mcp_server, shells out to `glab` only, runs under
bare python3 (same convention as omni_wait.py).

Behavior (spec D6):
- posts ONE summary note (glab api .../notes --method POST --raw-field body=...)
- posts one inline thread per finding via
  glab api .../discussions --method POST with the posting-guide nested-position
  workaround kept verbatim (--raw-field position[position_type]=text /
  position[base_sha] / position[head_sha] / position[start_sha] /
  position[new_path] / position[new_line]); diff refs are fetched ONCE per
  invocation — never per finding (N+1 discipline, mirrors the MCP fix)
- per-call retry with exponential backoff: up to --attempts (default 3),
  sleeping --backoff-base * 2^(attempt-1) seconds (2 s then 4 s by default);
  ONLY 5xx / 429 / transient-network stderr is retried — 4xx fails fast
  naming the failing command + response. (These are bounded posting retries
  inside one invocation — unrelated to the forbidden subagent sleep-poll.)
- duplicate-summary guard: when this invocation would post a summary, it
  first lists existing notes; a top-level note starting `## OmniForge` with
  created_at > --since (default 0) => REFUSE (exit 3, nothing posted) unless
  --force. Reply-only invocations (--reply-to, or a batch whose every entry
  carries reply_to_thread_id) never post a summary and never evaluate the
  guard — invocation 2 of an N-reply batch cannot trip on invocation 1's
  summary (the guard compares against --since, which predates the run).
- --reply-to <thread_id>: post the finding body/bodies as REPLY notes on
  that recorded discussion (.../discussions/<id>/notes) instead of new
  inline threads. Findings-json entries carrying reply_to_thread_id are
  routed the same way and EXCLUDED from new-thread posting (the batch form
  for OPEN prior findings). Resolved priors are the CALLER's skip decision —
  this script posts what it is given.
- --dry-run: print each exact command prefixed `DRY-RUN:` and execute
  nothing. Position SHA values print as <base_sha>/<head_sha>/<start_sha>
  placeholders (resolving them requires an API call, which dry-run never
  makes).

The script never adds text to any body — no AI attribution, ever; bodies are
caller-authored exactly as in the posting-guide templates.

Exit codes: 0 success; 1 posting failure (retries exhausted or 4xx
fail-fast); 2 usage; 3 duplicate-summary guard refusal.
Stdout: exactly one JSON line {"posted_summary", "threads", "replies",
"failures", "dry_run"}. Diagnostics go to stderr (omni_wait.py convention).
"""

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone

EXIT_OK, EXIT_API, EXIT_USAGE, EXIT_GUARD = 0, 1, 2, 3

SUMMARY_HEADING = "## OmniForge"
POSITION_TYPE = "position[position_type]=text"

# Retryable: HTTP 5xx, 429, transient-network stderr tokens (checked lowercase).
NETWORK_TOKENS = ("connection reset", "connection refused", "timeout",
                  "network", "eof", "dial tcp")
FATAL_4XX_RE = re.compile(r"\b(400|401|403|404)\b")
RETRYABLE_5XX_RE = re.compile(r"\b5\d\d\b")

# Dry-run placeholders for the run-time-resolved diff-ref SHAs.
PLACEHOLDER_REFS = {"base_sha": "<base_sha>", "head_sha": "<head_sha>",
                    "start_sha": "<start_sha>"}

sleep_fn = time.sleep          # module-level so tests can inject a recorder


class UsageError(Exception):
    """Bad invocation (missing/invalid arguments or input files) -> exit 2."""


class PostingError(Exception):
    """A glab call failed (retries exhausted, 4xx fail-fast, or bad payload)."""

    def __init__(self, argv, detail, returncode, attempts):
        self.command = " ".join(shlex.quote(a) for a in argv)
        super().__init__(
            "glab failed (%s; exit %d, attempt %d) — command: %s"
            % (detail, returncode, attempts, self.command))


def _exec(argv):
    p = subprocess.run(argv, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def is_retryable(stderr):
    """True iff stderr signals 5xx / 429 / a transient network error."""
    s = (stderr or "").lower()
    if FATAL_4XX_RE.search(s):
        return False
    if "429" in s or RETRYABLE_5XX_RE.search(s):
        return True
    return any(tok in s for tok in NETWORK_TOKENS)


def run_glab(argv, attempts, backoff_base):
    """Run one glab command with bounded exponential-backoff retry.

    Sleeps backoff_base * 2^(attempt-1) between attempts (2 s then 4 s at the
    defaults). Returns stdout on success; raises PostingError otherwise.
    """
    for attempt in range(1, attempts + 1):
        returncode, stdout, stderr = _exec(argv)
        if returncode == 0:
            return stdout
        if attempt < attempts and is_retryable(stderr):
            sleep_fn(backoff_base * (2 ** (attempt - 1)))
            continue
        raise PostingError(argv, stderr.strip() or "unknown error",
                           returncode, attempt)
    raise PostingError(argv, "no attempt made", 1, 0)   # unreachable


# ── command builders (posting-guide.md fallback shapes, verbatim) ──────────


def mr_endpoint(project, mr):
    return "projects/%s/merge_requests/%s" % (project, mr)


def refs_argv(project, mr):
    return ["glab", "api", mr_endpoint(project, mr)]


def notes_list_argv(project, mr):
    return ["glab", "api", mr_endpoint(project, mr) + "/notes?per_page=100"]


def summary_argv(project, mr, body):
    return ["glab", "api", mr_endpoint(project, mr) + "/notes",
            "--method", "POST", "--raw-field", "body=" + body]


def thread_argv(project, mr, body, refs, file_path, line_number):
    return ["glab", "api", mr_endpoint(project, mr) + "/discussions",
            "--method", "POST",
            "--raw-field", "body=" + body,
            "--raw-field", POSITION_TYPE,
            "--raw-field", "position[base_sha]=" + refs["base_sha"],
            "--raw-field", "position[head_sha]=" + refs["head_sha"],
            "--raw-field", "position[start_sha]=" + refs["start_sha"],
            "--raw-field", "position[new_path]=" + file_path,
            "--raw-field", "position[new_line]=" + str(line_number)]


def reply_argv(project, mr, thread_id, body):
    return ["glab", "api",
            mr_endpoint(project, mr) + "/discussions/" + thread_id + "/notes",
            "--method", "POST", "--raw-field", "body=" + body]


# ── API steps ──────────────────────────────────────────────────────────────


def fetch_diff_refs(project, mr, attempts, backoff_base):
    """Fetch diff_refs ONCE per invocation (never per finding)."""
    argv = refs_argv(project, mr)
    out = run_glab(argv, attempts, backoff_base)
    try:
        meta = json.loads(out)
    except json.JSONDecodeError:
        raise PostingError(argv, "MR metadata response is not JSON", 0, 0)
    refs = (meta or {}).get("diff_refs") or {}
    missing = [k for k in ("base_sha", "head_sha", "start_sha")
               if not refs.get(k)]
    if missing:
        raise PostingError(
            argv, "MR metadata lacks diff_refs.%s" % ", ".join(missing), 0, 0)
    return refs


def iso_to_epoch(value):
    """GitLab created_at -> epoch seconds; None when unparseable (never trips
    the guard on a timestamp we cannot prove is newer)."""
    s = (value or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def newer_summary_note(project, mr, since, attempts, backoff_base):
    """Return the OmniForge summary note newer than `since`, else None."""
    argv = notes_list_argv(project, mr)
    out = run_glab(argv, attempts, backoff_base)
    try:
        notes = json.loads(out)
    except json.JSONDecodeError:
        raise PostingError(argv, "notes list response is not JSON", 0, 0)
    if not isinstance(notes, list):
        raise PostingError(argv, "notes list response is not a JSON array", 0, 0)
    for note in notes:
        if not isinstance(note, dict):
            continue
        body = note.get("body") or ""
        if not body.lstrip().startswith(SUMMARY_HEADING):
            continue
        created = iso_to_epoch(note.get("created_at"))
        if created is not None and created > since:
            return note
    return None


# ── inputs ─────────────────────────────────────────────────────────────────


def parse_args(argv):
    ap = argparse.ArgumentParser(
        description="MCP-free GitLab MR review posting fallback (glab only; "
                    "one JSON line on stdout).")
    ap.add_argument("--mr", required=True, help="merge request IID")
    ap.add_argument("--project", required=True,
                    help="GitLab project ID or URL-encoded full path "
                         "(the literal :fullpath is passed through to glab)")
    ap.add_argument("--summary", required=True,
                    help="path to the summary markdown file")
    ap.add_argument("--findings-json", required=True,
                    help="path to the post_full_review findings array "
                         "[{file_path, line_number, body, "
                         "[reply_to_thread_id]}]")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the exact glab commands, execute nothing")
    ap.add_argument("--attempts", type=int, default=3,
                    help="max attempts per API call (default 3)")
    ap.add_argument("--backoff-base", type=float, default=2,
                    help="exponential backoff base seconds "
                         "(default 2: 2 s then 4 s)")
    ap.add_argument("--reply-to", default=None, metavar="THREAD_ID",
                    help="post the finding body/bodies as replies on this "
                         "existing discussion instead of new inline threads")
    ap.add_argument("--since", type=float, default=0,
                    help="run-start epoch for the duplicate-summary guard "
                         "(default 0: any existing OmniForge summary refuses)")
    ap.add_argument("--force", action="store_true",
                    help="override the duplicate-summary guard")
    args = ap.parse_args(argv)
    if args.attempts < 1:
        ap.error("--attempts must be >= 1")            # exits 2
    if args.backoff_base < 0:
        ap.error("--backoff-base must be >= 0")
    if args.reply_to is not None and not args.reply_to.strip():
        ap.error("--reply-to must be a non-empty thread id")
    return args


def load_summary(path):
    try:
        with open(path, encoding="utf-8") as fh:
            summary = fh.read()
    except OSError as e:
        raise UsageError("cannot read summary file %s: %s" % (path, e))
    if not summary.strip():
        raise UsageError("summary file %s is empty" % path)
    return summary


def load_findings(path):
    """Validate the findings array and split it into (new_threads, replies).

    Entries carrying reply_to_thread_id are routed as replies and EXCLUDED
    from new-thread posting. Invalid input is a usage error — the script
    never silently skips a finding.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise UsageError("cannot read findings JSON %s: %s" % (path, e))
    if not isinstance(data, list):
        raise UsageError("findings JSON %s must be a top-level array" % path)
    new_threads, replies = [], []
    for i, entry in enumerate(data, 1):
        if not isinstance(entry, dict):
            raise UsageError("finding %d: not an object" % i)
        body = entry.get("body")
        if not isinstance(body, str) or not body.strip():
            raise UsageError("finding %d: body is missing or empty" % i)
        thread_id = entry.get("reply_to_thread_id")
        if thread_id is not None:
            if not isinstance(thread_id, str) or not thread_id.strip():
                raise UsageError("finding %d: reply_to_thread_id must be a "
                                 "non-empty string" % i)
            replies.append({"thread_id": thread_id, "body": body})
            continue
        file_path = entry.get("file_path")
        line_number = entry.get("line_number")
        if not isinstance(file_path, str) or not file_path.strip():
            raise UsageError("finding %d: file_path is missing or empty" % i)
        if (not isinstance(line_number, int) or isinstance(line_number, bool)
                or line_number < 1):
            raise UsageError("finding %d: line_number must be an integer >= 1"
                             % i)
        new_threads.append({"file_path": file_path, "line_number": line_number,
                            "body": body})
    return new_threads, replies


def print_dry(argv):
    print("DRY-RUN: " + " ".join(shlex.quote(a) for a in argv))


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        summary = load_summary(args.summary)
        new_threads, replies = load_findings(args.findings_json)
    except UsageError as e:
        print("omni_post_review: %s" % e, file=sys.stderr)
        return EXIT_USAGE

    if args.reply_to:
        # --reply-to: every finding body becomes a reply on that thread.
        replies = ([{"thread_id": args.reply_to, "body": t["body"]}
                    for t in new_threads] + replies)
        new_threads = []

    reply_only = bool(args.reply_to) or (bool(replies) and not new_threads)
    will_post_summary = not reply_only

    counts = {"posted_summary": False, "threads": 0, "replies": 0,
              "failures": 0, "dry_run": bool(args.dry_run)}

    if args.dry_run:
        if will_post_summary:
            print_dry(notes_list_argv(args.project, args.mr))
        if new_threads:
            print_dry(refs_argv(args.project, args.mr))
        if will_post_summary:
            print_dry(summary_argv(args.project, args.mr, summary))
            counts["posted_summary"] = True
        for t in new_threads:
            print_dry(thread_argv(args.project, args.mr, t["body"],
                                  PLACEHOLDER_REFS, t["file_path"],
                                  t["line_number"]))
            counts["threads"] += 1
        for r in replies:
            print_dry(reply_argv(args.project, args.mr, r["thread_id"],
                                 r["body"]))
            counts["replies"] += 1
        print(json.dumps(counts))
        return EXIT_OK

    try:
        if will_post_summary and not args.force:
            note = newer_summary_note(args.project, args.mr, args.since,
                                      args.attempts, args.backoff_base)
            if note is not None:
                print("omni_post_review: REFUSING to post — an OmniForge "
                      "summary note (created %s) newer than --since %s "
                      "already exists on MR !%s; pass --force to override"
                      % (note.get("created_at"), args.since, args.mr),
                      file=sys.stderr)
                print(json.dumps(counts))
                return EXIT_GUARD
        refs = (fetch_diff_refs(args.project, args.mr, args.attempts,
                                args.backoff_base)
                if new_threads else None)
        if will_post_summary:
            run_glab(summary_argv(args.project, args.mr, summary),
                     args.attempts, args.backoff_base)
            counts["posted_summary"] = True
        for t in new_threads:
            run_glab(thread_argv(args.project, args.mr, t["body"], refs,
                                 t["file_path"], t["line_number"]),
                     args.attempts, args.backoff_base)
            counts["threads"] += 1
        for r in replies:
            run_glab(reply_argv(args.project, args.mr, r["thread_id"],
                                r["body"]), args.attempts, args.backoff_base)
            counts["replies"] += 1
    except PostingError as e:
        counts["failures"] += 1
        print("omni_post_review: %s" % e, file=sys.stderr)
        print(json.dumps(counts))
        return EXIT_API

    print(json.dumps(counts))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
