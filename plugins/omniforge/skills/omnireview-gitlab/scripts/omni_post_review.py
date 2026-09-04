#!/usr/bin/env python3
"""omni_post_review.py — GitLab MR review poster via the direct REST API.

TRANSPORT (W4): all traffic goes through the shared omni_glab_api transport
(direct stdlib-urllib REST — Bearer auth, bounded retry, redacted errors).
The former `glab api ... --raw-field position[...]` path is GONE: glab drops
nested position keys, so those threads landed UNANCHORED. Inline threads are
now created via POST .../discussions with the documented position[…] form
keys sent form-encoded with literal bracket keys — anchored first-try.

Behavior (spec D6, preserved through the W4 transport swap):
- posts ONE summary note (POST .../notes, form body=<summary>)
- posts one inline thread per finding via POST .../discussions with form
  pairs: body, position[position_type]=text, position[base_sha|start_sha|
  head_sha|new_path|old_path], position[new_line] (or position[old_line]
  when the entry carries old_line); diff refs are fetched ONCE per
  invocation — never per finding (N+1 discipline, mirrors the MCP fix).
  line_range is NOT sent (3.3.2 candidate).
- NOTE entries (3.3.2): findings objects carrying ONLY {"body": ...} (no
  file_path/line_number) post as top-level MR notes (POST .../notes, form
  body=...) — the poster's replacement for raw-glab general notes, whose
  `glab api --input -` path silently drops note bodies (production: !1388
  lost 5/5 notes). Execution order: summary → threads → replies → notes
  (notes last). A body-only entry that also carries file_path/line_number
  (or old_path/old_line) is an ambiguous-shape usage error — never silently
  skipped.
- retry/backoff and 4xx fail-fast live in omni_glab_api.request: up to
  --attempts (default 3), sleeping --backoff-base * 2^(attempt-1) seconds
  (2 s then 4 s by default); ONLY 5xx / 429 / transient network is retried —
  400/401/403/404 fails fast naming the failing call.
- duplicate-summary guard: when this invocation is not reply-only, it first
  lists existing notes (omni_glab_api.get_all — per_page=100, paginated, so
  a busy MR cannot hide an OmniForge summary on page 2; a single page costs
  exactly ONE GET); a top-level note starting `## OmniForge` with
  created_at > --since (default 0) => REFUSE (exit 3, nothing posted) unless
  --force. Reply-only invocations (--reply-to, or a batch whose every entry
  carries reply_to_thread_id) never post a summary and never evaluate the
  guard. --skip-summary posts threads/replies only but STILL evaluates the
  guard unless --force (mid-batch resume = --skip-summary --force, which
  cannot repost the summary).
- auto-summary-skip (3.3.2): --summary is required ONLY when the batch has
  at least one new-thread entry; a reply-only and/or note-only batch runs
  without --summary via an implied skip (no summary, guard not evaluated —
  the friction this removes: reply-only batches dying on argparse exit 2).
  New threads present without --summary is still a usage error;
  --skip-summary keeps its current meaning.
- --reply-to <thread_id>: post the finding body/bodies as REPLY notes on
  that recorded discussion (.../discussions/<id>/notes) instead of new
  inline threads. Findings-json entries carrying reply_to_thread_id are
  routed the same way and EXCLUDED from new-thread posting.
- --host (3.3.2): same resolution order as omni_fetch_mr.py (flag >
  GITLAB_HOST > CI_API_V4_URL > https://gitlab.com), threaded through EVERY
  api call including the guard's notes listing and note posts.
- --dry-run: print each logical call as `DRY-RUN: <METHOD> <url> <k=v form
  pairs>` and execute NOTHING (zero transport calls; the token is never
  resolved on this path). Position SHA values print as
  <base_sha>/<head_sha>/<start_sha> placeholders (resolving them requires
  an API call, which dry-run never makes).

The script never adds text to any body — no AI attribution, ever; bodies are
caller-authored exactly as in the posting-guide templates.

Exit codes: 0 success; 1 posting failure (retries exhausted or 4xx
fail-fast); 2 usage (including a missing token on real runs — dry-run works
without one); 3 duplicate-summary guard refusal.
Stdout: exactly one JSON line {"posted_summary", "threads", "replies",
"notes", "failures", "dry_run", "elapsed_ms"} on exits 0/1/3; exit 2 (usage)
prints no stdout JSON — consumers parse stdout only on non-usage exits.
Diagnostics go to stderr (omni_wait.py convention).
"""

import argparse
import json
import os
import shlex
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omni_glab_api

EXIT_OK, EXIT_API, EXIT_USAGE, EXIT_GUARD = 0, 1, 2, 3

SUMMARY_HEADING = "## OmniForge"

# Dry-run placeholders for the run-time-resolved diff-ref SHAs.
PLACEHOLDER_REFS = {"base_sha": "<base_sha>", "head_sha": "<head_sha>",
                    "start_sha": "<start_sha>"}

TOKEN_FIX = ("export GITLAB_TOKEN=$(glab auth status --hostname <host> -t "
             "2>/dev/null | sed -n 's/^.*- Token: //p')")


class UsageError(Exception):
    """Bad invocation (missing/invalid arguments or input files) -> exit 2."""


class PostingError(Exception):
    """A payload-level failure (non-JSON / malformed API response)."""


# ── paths + form builders (documented Discussions API spellings) ───────────


def mr_path(project, mr):
    return "projects/%s/merge_requests/%s" % (project, mr)


def refs_path(project, mr):
    return mr_path(project, mr)


def notes_base_path(project, mr):
    return mr_path(project, mr) + "/notes"


def notes_list_path(project, mr):
    """The guard's notes listing, first page — the exact path get_all builds
    on notes_base_path (per_page=100&page=1)."""
    return notes_base_path(project, mr) + "?per_page=100&page=1"


def summary_path(project, mr):
    return mr_path(project, mr) + "/notes"


def discussions_path(project, mr):
    return mr_path(project, mr) + "/discussions"


def reply_path(project, mr, thread_id):
    return mr_path(project, mr) + "/discussions/" + thread_id + "/notes"


def thread_form(body, refs, file_path, line_number,
                old_path=None, old_line=None):
    """Form pairs for an ANCHORED inline thread — literal bracket keys,
    exactly the documented REST spellings (position[old_line] replaces
    position[new_line] for deleted-file findings; line_range is NOT sent)."""
    form = [("body", body),
            ("position[position_type]", "text"),
            ("position[base_sha]", refs["base_sha"]),
            ("position[start_sha]", refs["start_sha"]),
            ("position[head_sha]", refs["head_sha"]),
            ("position[new_path]", file_path),
            ("position[old_path]", old_path or file_path)]
    if old_line is not None:
        form.append(("position[old_line]", str(old_line)))
    else:
        form.append(("position[new_line]", str(line_number)))
    return form


# ── API steps (all via the shared transport) ───────────────────────────────


def api(method, path, token, host, form, attempts, backoff_base):
    return omni_glab_api.request(method, path, token, host=host, form=form,
                                 attempts=attempts,
                                 backoff_base=backoff_base)


def fetch_diff_refs(project, mr, token, host, attempts, backoff_base):
    """Fetch diff_refs ONCE per invocation (never per finding)."""
    path = refs_path(project, mr)
    resp = api("GET", path, token, host, None, attempts, backoff_base)
    meta = resp.get("json")
    if not isinstance(meta, dict):
        raise PostingError("GET %s: MR metadata response is not JSON" % path)
    refs = meta.get("diff_refs") or {}
    missing = [k for k in ("base_sha", "head_sha", "start_sha")
               if not refs.get(k)]
    if missing:
        raise PostingError(
            "GET %s: MR metadata lacks diff_refs.%s" % (path, ", ".join(missing)))
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


def newer_summary_note(project, mr, since, token, host, attempts, backoff_base):
    """Return the OmniForge summary note newer than `since`, else None.

    The listing paginates via omni_glab_api.get_all (per_page=100) — a busy
    MR with 100+ notes can push a prior OmniForge summary onto page 2, and
    the guard must still find it. A single page still costs exactly ONE GET
    (get_all stops at the first short page)."""
    notes = omni_glab_api.get_all(notes_base_path(project, mr), token,
                                  host=host, attempts=attempts,
                                  backoff_base=backoff_base)
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
        description="GitLab MR review poster via the direct Discussions API "
                    "(one JSON line on stdout).")
    ap.add_argument("--mr", required=True, help="merge request IID")
    ap.add_argument("--project", required=True,
                    help="GitLab project ID or URL-encoded full path")
    ap.add_argument("--host", default=None,
                    help="GitLab host (default: GITLAB_HOST env, "
                         "CI_API_V4_URL env, then https://gitlab.com)")
    ap.add_argument("--summary", default=None,
                    help="path to the summary markdown file (required only "
                         "when the batch posts new threads; reply-only and "
                         "note-only batches skip it automatically)")
    ap.add_argument("--skip-summary", action="store_true",
                    help="post threads/replies only, no summary note (the "
                         "duplicate-summary guard still applies unless "
                         "--force)")
    ap.add_argument("--findings-json", required=True,
                    help="path to the findings array [{file_path, "
                         "line_number, body, [reply_to_thread_id], "
                         "[old_path], [old_line]}]; an entry carrying ONLY "
                         "body posts as a top-level MR note")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the exact API calls, execute nothing "
                         "(works without a token)")
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
    if args.skip_summary and args.reply_to is not None:
        ap.error("--skip-summary cannot be combined with --reply-to "
                 "(reply-only runs never post a summary anyway)")
    # NOTE: --summary is NOT checked here — since 3.3.2 the requirement is
    # batch-dependent (only new-thread entries require it); main() enforces
    # it once the findings are loaded.
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
    """Validate the findings array and split it into (new_threads, replies,
    notes).

    Entries carrying reply_to_thread_id are routed as replies and EXCLUDED
    from new-thread posting. Entries carrying ONLY body (no file_path, no
    line_number) are routed as note entries — top-level MR notes. Optional
    old_path (default = file_path) and old_line are forwarded when present.
    Invalid input is a usage error — the script never silently skips a
    finding.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise UsageError("cannot read findings JSON %s: %s" % (path, e))
    if not isinstance(data, list):
        raise UsageError("findings JSON %s must be a top-level array" % path)
    new_threads, replies, notes = [], [], []
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
        if file_path is None and line_number is None:
            # note entry — must carry ONLY body; any anchor-ish key on a
            # body-only entry is an ambiguous shape, never a silent skip
            if entry.get("old_path") is not None \
                    or entry.get("old_line") is not None:
                raise UsageError(
                    "finding %d: ambiguous entry — note entries carry only "
                    "body; thread entries need BOTH file_path and "
                    "line_number" % i)
            notes.append({"body": body})
            continue
        if not isinstance(file_path, str) or not file_path.strip():
            raise UsageError(
                "finding %d: ambiguous entry — note entries carry only "
                "body; thread entries need BOTH file_path and line_number "
                "(file_path is missing)" % i)
        if (not isinstance(line_number, int) or isinstance(line_number, bool)
                or line_number < 1):
            raise UsageError("finding %d: line_number must be an integer >= 1"
                             % i)
        old_path = entry.get("old_path")
        if old_path is not None and (not isinstance(old_path, str)
                                     or not old_path.strip()):
            raise UsageError("finding %d: old_path must be a non-empty "
                             "string" % i)
        old_line = entry.get("old_line")
        if old_line is not None and (not isinstance(old_line, int)
                                     or isinstance(old_line, bool)
                                     or old_line < 1):
            raise UsageError("finding %d: old_line must be an integer >= 1"
                             % i)
        new_threads.append({"file_path": file_path, "line_number": line_number,
                            "body": body, "old_path": old_path,
                            "old_line": old_line})
    return new_threads, replies, notes


# ── dry-run ────────────────────────────────────────────────────────────────


def dry_line(method, url, form=None):
    # One LOGICAL call per "DRY-RUN: " chunk — bodies containing newlines
    # make a chunk span physical lines, so consumers chunk on the prefix,
    # never on newlines.
    if form:
        pairs = " ".join("%s=%s" % (k, shlex.quote(v)) for k, v in form)
        print("DRY-RUN: %s %s %s" % (method, url, pairs))
    else:
        print("DRY-RUN: %s %s" % (method, url))


def main(argv=None):
    start = time.time()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        summary = load_summary(args.summary) if args.summary else None
        new_threads, replies, notes = load_findings(args.findings_json)
        if args.reply_to:
            # --reply-to: every finding body becomes a reply on that thread.
            replies = ([{"thread_id": args.reply_to, "body": t["body"]}
                        for t in new_threads] + replies)
            new_threads = []
        # 3.3.2: --summary is required ONLY when the batch posts new
        # threads. Without new-thread entries the run proceeds via an
        # implied skip (no summary, guard not evaluated) — the same
        # frictionless path --skip-summary never gave reply-only batches.
        if not args.summary and not args.skip_summary and new_threads:
            raise UsageError("--summary is required when posting new "
                             "threads (or pass --skip-summary)")
    except UsageError as e:
        print("omni_post_review: %s" % e, file=sys.stderr)
        return EXIT_USAGE

    reply_only = bool(args.reply_to) or (bool(replies) and not new_threads)
    implied_skip = (not args.summary and not args.skip_summary
                    and not new_threads)
    will_post_summary = (not reply_only and not args.skip_summary
                         and not implied_skip)
    # The guard applies to every invocation that is neither reply-only nor
    # implied-skip (including --skip-summary) unless --force — mid-batch
    # resume is --skip-summary --force, which cannot repost the summary.
    guard_applies = not reply_only and not implied_skip and not args.force

    counts = {"posted_summary": False, "threads": 0, "replies": 0,
              "notes": 0, "failures": 0, "dry_run": bool(args.dry_run)}

    def emit():
        counts["elapsed_ms"] = int(round((time.time() - start) * 1000))
        print(json.dumps(counts))
        return counts

    if args.dry_run:
        # Token resolution is DEFERRED past this path — dry-run executes
        # nothing and works without a token.
        if guard_applies:
            dry_line("GET", notes_list_path(args.project, args.mr))
        if new_threads:
            dry_line("GET", refs_path(args.project, args.mr))
        if will_post_summary:
            dry_line("POST", summary_path(args.project, args.mr),
                     [("body", summary)])
            counts["posted_summary"] = True
        for t in new_threads:
            dry_line("POST", discussions_path(args.project, args.mr),
                     thread_form(t["body"], PLACEHOLDER_REFS, t["file_path"],
                                 t["line_number"], t["old_path"],
                                 t["old_line"]))
            counts["threads"] += 1
        for r in replies:
            dry_line("POST", reply_path(args.project, args.mr, r["thread_id"]),
                     [("body", r["body"])])
            counts["replies"] += 1
        for n in notes:
            dry_line("POST", summary_path(args.project, args.mr),
                     [("body", n["body"])])
            counts["notes"] += 1
        emit()
        return EXIT_OK

    token = omni_glab_api.resolve_token()
    if not token:
        print("omni_post_review: GITLAB_TOKEN is not set — fix:\n  %s"
              % TOKEN_FIX, file=sys.stderr)
        return EXIT_USAGE

    try:
        if guard_applies:
            note = newer_summary_note(args.project, args.mr, args.since,
                                      token, args.host, args.attempts,
                                      args.backoff_base)
            if note is not None:
                print("omni_post_review: REFUSING to post — an OmniForge "
                      "summary note (created %s) newer than --since %s "
                      "already exists on MR !%s; pass --force to override"
                      % (note.get("created_at"), args.since, args.mr),
                      file=sys.stderr)
                emit()
                return EXIT_GUARD
        refs = (fetch_diff_refs(args.project, args.mr, token, args.host,
                                args.attempts, args.backoff_base)
                if new_threads else None)
        if will_post_summary:
            api("POST", summary_path(args.project, args.mr), token, args.host,
                [("body", summary)], args.attempts, args.backoff_base)
            counts["posted_summary"] = True
        for t in new_threads:
            api("POST", discussions_path(args.project, args.mr), token,
                args.host,
                thread_form(t["body"], refs, t["file_path"], t["line_number"],
                            t["old_path"], t["old_line"]),
                args.attempts, args.backoff_base)
            counts["threads"] += 1
        for r in replies:
            api("POST", reply_path(args.project, args.mr, r["thread_id"]),
                token, args.host, [("body", r["body"])], args.attempts,
                args.backoff_base)
            counts["replies"] += 1
        for n in notes:
            api("POST", summary_path(args.project, args.mr), token,
                args.host, [("body", n["body"])], args.attempts,
                args.backoff_base)
            counts["notes"] += 1
    except (omni_glab_api.GlabApiError, PostingError) as e:
        counts["failures"] += 1
        print("omni_post_review: %s" % e, file=sys.stderr)
        emit()
        return EXIT_API

    emit()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
