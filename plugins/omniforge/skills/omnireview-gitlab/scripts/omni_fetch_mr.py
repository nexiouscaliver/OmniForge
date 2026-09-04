#!/usr/bin/env python3
"""omni_fetch_mr.py — one-shot GitLab MR gather (W4 T1).

ONE invocation gathers everything Phase 1 needs into ONE JSON file: MR
metadata, the assembled unified diff, diff_line_map, commits, AND every
discussion thread (plus versions and top-level notes). Stdlib only; direct
REST via the shared omni_glab_api transport (no glab subprocess, no MCP).

CLI:
  python3 omni_fetch_mr.py --project <id-or-fullpath> --mr <iid> \
      --out <path> [--host <host>] [--attempts 3] [--backoff-base 2.0] \
      [--max-diff-lines 10000] [--verify-head <recorded_sha>]

--project accepts a numeric ID, a pre-encoded URL path, or a bare full path
(group/subgroup/project — URL-encoded automatically before it reaches any
endpoint).

Exit/stdout contract (mirrors poster conventions):
  0  gather OK — file written (atomic: <out>.tmp then os.replace) + exactly
      one stdout JSON line {ok:true, out, project, mr_iid, head_sha,
      api_calls, diff_line_count, files_changed, discussions_total,
      discussions_unresolved, versions, elapsed_ms}; verify-head equal —
      {ok:true, head_moved:false, recorded_head, current_head, elapsed_ms}.
  1   API failure (retries exhausted / 4xx fail-fast): {ok:false, error,
      endpoint} + stderr diagnostics.
  2   usage/precondition (flags, token, unwritable out): no stdout JSON.
  4   verify-head moved: {ok:false, head_moved:true, recorded_head,
      current_head}.

Token: GITLAB_TOKEN env, fallback OMNIFORGE_GITLAB_TOKEN; missing → exit 2
with the glab extraction one-liner on stderr.

Gather endpoints (all GET, verified against the official docs):
  /projects/:p/merge_requests/:m                    metadata
  /projects/:p/merge_requests/:m/diffs?per_page=100&page=N
  /projects/:p/merge_requests/:m/discussions?per_page=100&page=N
  /projects/:p/merge_requests/:m/commits?per_page=100&page=N
  /projects/:p/merge_requests/:m/versions?per_page=100     (first page)
  /projects/:p/merge_requests/:m/notes?per_page=100        (first page)

A diff/discussions/commits page that still fails its per-call retries gets
exactly ONE additional retry of that page (same schedule) before exit 1.
"""

import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omni_glab_api

SCHEMA = "omniforge-mr-gather/1"
MAX_DIFF_LINES = 10000
MAX_DIFF_CHARS = 150000


# ── pure functions ported from tools/omniforge_mcp_server.py ────────────
# (copied, not imported — the standalone script never imports the MCP
# server; keep the two implementations byte-equivalent)


def extract_changed_files(diff_text):
    """Extract file paths from unified diff output."""
    files = []
    for line in diff_text.split('\n'):
        if line.startswith('+++ b/'):
            path = line[6:]
            if path not in files:
                files.append(path)
    return files


def parse_commits(log_output):
    """Parse git log --oneline output into structured commits."""
    commits = []
    for line in log_output.strip().split('\n'):
        if line.strip():
            parts = line.split(' ', 1)
            commits.append({
                "sha": parts[0],
                "message": parts[1] if len(parts) > 1 else "",
            })
    return commits


def parse_diff_line_map(diff_text):
    """Parse unified diff and return changed line numbers per file.

    Returns a dict keyed by file path, each containing:
      - added_lines: list of line numbers that were added (+ lines)
      - all_new_lines: list of all line numbers visible in the new file
        (context + added, excludes deleted lines)
      - hunks: list of {new_start, new_count} for each hunk

    Line numbers refer to the NEW version of the file (what GitLab's
    position[new_line] expects for inline discussion threads).
    """
    if not diff_text or not diff_text.strip():
        return {}

    result = {}
    current_file = None
    new_line_num = 0

    for line in diff_text.split('\n'):
        # New file header: +++ b/path/to/file
        if line.startswith('+++ b/'):
            current_file = line[6:]
            if current_file not in result:
                result[current_file] = {
                    "added_lines": [],
                    "all_new_lines": [],
                    "hunks": [],
                }

        # Hunk header: @@ -old_start,old_count +new_start,new_count @@
        elif line.startswith('@@') and current_file:
            # Parse +new_start,new_count
            parts = line.split('+')[1].split('@@')[0].strip()
            if ',' in parts:
                new_start, new_count = parts.split(',')
            else:
                new_start, new_count = parts, '1'
            new_start = int(new_start)
            new_count = int(new_count)
            new_line_num = new_start
            result[current_file]["hunks"].append({
                "new_start": new_start,
                "new_count": new_count,
            })

        # Added line (exists in new file)
        elif line.startswith('+') and not line.startswith('+++') and current_file:
            result[current_file]["added_lines"].append(new_line_num)
            result[current_file]["all_new_lines"].append(new_line_num)
            new_line_num += 1

        # Deleted line (only in old file — does NOT advance new line counter)
        elif line.startswith('-') and not line.startswith('---') and current_file:
            pass  # deleted lines don't exist in new file

        # Context line (unchanged, exists in both)
        elif current_file and not line.startswith('\\') and not line.startswith('diff ') and not line.startswith('index ') and not line.startswith('---'):
            if new_line_num > 0:  # only if we're inside a hunk
                result[current_file]["all_new_lines"].append(new_line_num)
                new_line_num += 1

    return result


def truncate_diff_if_needed(diff_text, line_count, max_lines=MAX_DIFF_LINES,
                            max_chars=MAX_DIFF_CHARS):
    """Truncate diff if it exceeds the line/char ceilings (ported from the
    MCP server; the ceilings are parameters so --max-diff-lines drives the
    same truncation)."""
    truncated = False
    reason = ""

    if line_count > max_lines:
        lines = diff_text.split('\n')[:max_lines]
        diff_text = '\n'.join(lines)
        truncated = True
        reason = f"{line_count} total lines, showing first {max_lines}"

    if len(diff_text) > max_chars:
        original_chars = len(diff_text)
        cut_point = diff_text.rfind('\n', 0, max_chars)
        if cut_point == -1:
            cut_point = max_chars
        diff_text = diff_text[:cut_point]
        truncated = True
        char_reason = f"{len(diff_text)} of {original_chars} chars shown"
        reason = f"{reason}; {char_reason}" if reason else char_reason

    if truncated:
        diff_text += f"\n\n... [TRUNCATED: {reason}] ..."

    return diff_text, truncated


# ── discussions envelope (ported parsing from the MCP server) ───────────


def parse_discussions(raw_discussions):
    """Raw GitLab discussions array -> the v3.3.0 fetch_mr_discussions
    item shape; system notes dropped; inline detection =
    position.new_path present or type == "DiffNote"."""
    discussions = []
    for disc in raw_discussions or []:
        if not isinstance(disc, dict):
            continue
        notes = [n for n in disc.get("notes", [])
                 if isinstance(n, dict) and not n.get("system", False)]
        if not notes:
            continue
        first_note = notes[0]
        position = first_note.get("position") or {}
        note_type = first_note.get("type", "")

        has_position = bool(position and position.get("new_path"))
        is_diff_note = note_type == "DiffNote"
        is_inline = has_position or is_diff_note

        discussions.append({
            "id": disc.get("id", ""),
            "resolvable": disc.get("resolvable", False),
            "resolved": disc.get("resolved", False),
            "type": "inline" if is_inline else "general",
            "file_path": position.get("new_path"),
            "line_number": position.get("new_line"),
            "body": first_note.get("body", ""),
            "author": (first_note.get("author") or {}).get("username", ""),
            "created_at": first_note.get("created_at", ""),
            "replies": [
                {
                    "author": (n.get("author") or {}).get("username", ""),
                    "body": n.get("body", ""),
                    "created_at": n.get("created_at", ""),
                }
                for n in notes[1:]
            ],
        })
    return discussions


def assemble_diff(diff_pages):
    """Concatenate the per-file diff strings in server order. The /diffs
    API returns each file's diff starting directly at the @@ hunks (dev-
    verified on gitlab.com 2026-09-03: no `diff --git`/`+++ b/` headers),
    so a unified-diff header is synthesized from the item's old_path/
    new_path — parse_diff_line_map/extract_changed_files rely on the
    `+++ b/` marker. Items already carrying full headers pass through."""
    parts = []
    for item in diff_pages:
        if not isinstance(item, dict):
            continue
        d = item.get("diff")
        if not isinstance(d, str) or not d.strip():
            continue                     # e.g. pure rename, no hunks
        if not d.startswith(("diff --git", "---", "+++")):
            old_path = item.get("old_path") or item.get("new_path") or ""
            new_path = item.get("new_path") or old_path
            header = "diff --git a/%s b/%s\n" % (old_path, new_path)
            if item.get("new_file"):
                header += "new file mode 100644\n"
                header += "--- /dev/null\n+++ b/%s\n" % new_path
            elif item.get("deleted_file"):
                header += ("deleted file mode 100644\n--- a/%s\n"
                           "+++ /dev/null\n" % old_path)
            else:
                header += "--- a/%s\n+++ b/%s\n" % (old_path, new_path)
            d = header + d
        parts.append(d if d.endswith("\n") else d + "\n")
    return "".join(parts)


# ── gather engine ────────────────────────────────────────────────────────


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="One-shot GitLab MR gather (metadata, diff, "
                    "discussions, commits, versions, notes) into a single "
                    "JSON file.")
    ap.add_argument("--project", required=True,
                    help="project ID, pre-encoded URL path, or bare full "
                         "path (group/subgroup/project — URL-encoded "
                         "automatically)")
    ap.add_argument("--mr", required=True, help="merge request IID")
    ap.add_argument("--out", default=None,
                    help="gather JSON output path (required in gather mode; "
                         "not used with --verify-head)")
    ap.add_argument("--host", default=None,
                    help="GitLab host (default: GITLAB_HOST env, "
                         "CI_API_V4_URL env, then https://gitlab.com)")
    ap.add_argument("--attempts", type=int, default=3,
                    help="per-call retry attempts (default 3)")
    ap.add_argument("--backoff-base", type=float, default=2.0,
                    help="retry backoff base in seconds (default 2.0)")
    ap.add_argument("--max-diff-lines", type=int, default=10000,
                    help="diff truncation line ceiling (default 10000)")
    ap.add_argument("--verify-head", default=None, metavar="RECORDED_SHA",
                    help="verify mode: ONE metadata GET comparing the MR's "
                         "current head SHA against the recorded one; "
                         "writes nothing")
    return ap.parse_args(argv)


class Gatherer:
    def __init__(self, args, token):
        self.args = args
        self.token = token
        self.api_calls = 0

    def _request(self, method, path, form=None):
        self.api_calls += 1
        return omni_glab_api.request(
            method, path, self.token, host=self.args.host, form=form,
            attempts=self.args.attempts,
            backoff_base=self.args.backoff_base)

    def _page(self, path):
        """One paginated GET. A page that exhausted its per-call retries
        (retryable GlabApiError) gets exactly ONE additional retry of the
        whole page (same schedule); fail-fast 4xx propagates."""
        try:
            return self._request("GET", path)
        except omni_glab_api.GlabApiError as e:
            if not e.retryable:
                raise
            return self._request("GET", path)

    def _pages(self, base):
        items, page = [], 1
        while True:
            sep = "&" if "?" in base else "?"
            batch = self._page(
                "%s%sper_page=100&page=%d" % (base, sep, page)).get("json")
            if not isinstance(batch, list):
                raise omni_glab_api.GlabApiError(
                    "GET", base, 0, "expected a JSON array", retryable=False)
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    def _first_page(self, base):
        sep = "&" if "?" in base else "?"
        batch = self._page("%s%sper_page=100" % (base, sep)).get("json")
        return batch if isinstance(batch, list) else []


def mr_path(project, mr):
    # Full-path projects are URL-encoded by the ONE shared helper in
    # omni_glab_api (moved there in 3.3.3 so the poster encodes too).
    return "/projects/%s/merge_requests/%s" % (
        omni_glab_api.encode_project(project), mr)


def token_fix_line(host):
    return ("omni_fetch_mr: GITLAB_TOKEN not set — extract it from the "
            "authenticated host glab (never printed):\n"
            "export GITLAB_TOKEN=$(glab auth status --hostname %s -t "
            "2>/dev/null | sed -n 's/^.*- Token: //p')" % host)


def _fail(e):
    print("omni_fetch_mr: %s" % e, file=sys.stderr)
    print(json.dumps({"ok": False, "error": str(e), "endpoint": e.path}))
    return 1


def _verify_head(args, token, started):
    g = Gatherer(args, token)
    try:
        meta = g._request("GET", mr_path(args.project, args.mr)).get("json") \
            or {}
    except omni_glab_api.GlabApiError as e:
        return _fail(e)
    diff_refs = meta.get("diff_refs") or {}
    current = diff_refs.get("head_sha") or ""
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if current == args.verify_head:
        print(json.dumps({"ok": True, "head_moved": False,
                          "recorded_head": args.verify_head,
                          "current_head": current,
                          "elapsed_ms": elapsed_ms}))
        return 0
    print(json.dumps({"ok": False, "head_moved": True,
                      "recorded_head": args.verify_head,
                      "current_head": current}))
    return 4


def _gather(args, token, started):
    g = Gatherer(args, token)
    try:
        meta = g._request("GET", mr_path(args.project, args.mr)).get("json") \
            or {}
        diff_pages = g._pages(mr_path(args.project, args.mr) + "/diffs")
        raw_discussions = g._pages(
            mr_path(args.project, args.mr) + "/discussions")
        raw_commits = g._pages(mr_path(args.project, args.mr) + "/commits")
        versions = g._first_page(mr_path(args.project, args.mr) + "/versions")
        notes = g._first_page(mr_path(args.project, args.mr) + "/notes")
    except omni_glab_api.GlabApiError as e:
        return _fail(e)

    diff_refs = meta.get("diff_refs") or {}
    raw_diff = assemble_diff(diff_pages)
    diff_lines = raw_diff.count('\n')
    diff_text, diff_truncated = truncate_diff_if_needed(
        raw_diff, diff_lines, max_lines=args.max_diff_lines)
    diff_line_map = parse_diff_line_map(raw_diff)
    files_changed = extract_changed_files(raw_diff)
    commits = [{"sha": c.get("id", ""), "message": c.get("title", "")}
               for c in raw_commits if isinstance(c, dict)]
    comments = "\n\n".join(
        n.get("body", "") for n in notes
        if isinstance(n, dict) and not n.get("system", False)
        and isinstance(n.get("body"), str))
    pipeline = meta.get("head_pipeline")
    pipeline_status = pipeline.get("status", "unknown") \
        if isinstance(pipeline, dict) else "unknown"

    discussions = parse_discussions(raw_discussions)
    unresolved = sum(1 for d in discussions
                     if d["resolvable"] and not d["resolved"])
    resolved = sum(1 for d in discussions
                   if d["resolvable"] and d["resolved"])

    data = {
        "success": True,
        "mr_id": args.mr,
        "title": meta.get("title", ""),
        "author": (meta.get("author") or {}).get("username", ""),
        "source_branch": meta.get("source_branch", ""),
        "target_branch": meta.get("target_branch", ""),
        "pipeline_status": pipeline_status,
        "description": meta.get("description", "") or "",
        "comments": comments,
        "diff": diff_text,
        "diff_line_count": diff_lines,
        "diff_too_large": diff_lines > args.max_diff_lines,
        "diff_truncated": diff_truncated,
        "diff_line_map": diff_line_map,
        "commits": commits,
        "files_changed": files_changed,
        "labels": meta.get("labels", []) or [],
        "assignees": [a.get("username", "") for a in meta.get("assignees")
                      or [] if isinstance(a, dict)],
        "reviewers": [r.get("username", "") for r in meta.get("reviewers")
                      or [] if isinstance(r, dict)],
        "diff_refs": diff_refs,
    }

    gather = {
        "schema": SCHEMA,
        "fetched_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "project": args.project,
        "mr_iid": args.mr,
        "diff_refs": diff_refs,
        "data": data,
        "discussions": {
            "success": True,
            "mr_id": args.mr,
            "discussions": discussions,
            "total": len(discussions),
            "unresolved": unresolved,
            "resolved": resolved,
        },
        "versions": versions,
    }

    tmp = args.out + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(gather, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, args.out)
    except OSError as e:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        print("omni_fetch_mr: cannot write out: %s" % e, file=sys.stderr)
        return 2

    elapsed_ms = int((time.monotonic() - started) * 1000)
    print(json.dumps({
        "ok": True,
        "out": args.out,
        "project": args.project,
        "mr_iid": args.mr,
        "head_sha": diff_refs.get("head_sha", ""),
        "api_calls": g.api_calls,
        "diff_line_count": diff_lines,
        "files_changed": len(files_changed),
        "discussions_total": len(discussions),
        "discussions_unresolved": unresolved,
        "versions": len(versions),
        "elapsed_ms": elapsed_ms,
    }))
    return 0


def main(argv=None):
    args = parse_args(argv)
    started = time.monotonic()
    token = omni_glab_api.resolve_token()
    if not token:
        host = omni_glab_api.resolve_host(args.host)
        print(token_fix_line(host), file=sys.stderr)
        return 2
    if args.verify_head is not None:
        return _verify_head(args, token, started)
    if not args.out:
        print("omni_fetch_mr: --out is required in gather mode",
              file=sys.stderr)
        return 2
    return _gather(args, token, started)


if __name__ == "__main__":
    sys.exit(main())
