#!/usr/bin/env python3
"""OmniForge MCP Server — worktree and MR data tools for the merge request lifecycle."""

import asyncio
import json
import mimetypes
import os
import re
import shutil
import tempfile

# ── Constants ──────────────────────────────────────────────

WORKTREE_TYPES = ["analyst", "codebase", "security"]
MAX_DIFF_LINES = 10000
MAX_DIFF_CHARS = 150000

# Environment variable holding the OmniCheck bot's api-scoped GitLab token.
# When set, approve_mr authenticates as the bot for that single call so it can
# approve MRs whose author/commit settings block the current glab user.
OMNICHECK_BOT_TOKEN_ENV = "OMNICHECK_BOT_TOKEN"

# ── Input Validation ──────────────────────────────────────


def validate_mr_id(mr_id: str) -> str:
    """Strip leading '!' and validate mr_id is numeric."""
    mr_id = mr_id.lstrip('!')
    if not re.match(r'^\d+$', mr_id):
        raise ValueError(f"Invalid MR ID: {mr_id}. Must be numeric.")
    return mr_id


def validate_repo_root(repo_root: str) -> str:
    """Validate repo_root is an absolute path to a git repository."""
    if not os.path.isabs(repo_root):
        raise ValueError(f"repo_root must be absolute: {repo_root}")
    git_path = os.path.join(repo_root, ".git")
    if not (os.path.isdir(git_path) or os.path.isfile(git_path)):
        raise ValueError(f"Not a git repository: {repo_root}")
    return repo_root


async def _resolve_main_repo_root(repo_root: str) -> str:
    """If repo_root is a worktree, return the main repository root.

    Otherwise return repo_root unchanged.

    Uses ``git rev-parse --git-common-dir`` which always resolves to the
    shared/common git directory regardless of whether *repo_root* is the
    main checkout or a linked worktree.  The main repo root is the parent
    directory of that common git dir.
    """
    r = await run_exec(
        ["git", "rev-parse", "--git-common-dir"], cwd=repo_root, timeout=10,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return repo_root
    # git may return a relative path (e.g. ".git"); resolve against repo_root
    common_dir = os.path.abspath(os.path.join(repo_root, r.stdout.strip()))
    main_root = os.path.dirname(common_dir)
    # Guard against the unlikely case where basename is still ".git"
    if os.path.basename(main_root) == ".git":
        main_root = os.path.dirname(main_root)
    if os.path.abspath(main_root) == os.path.abspath(repo_root):
        return repo_root
    return main_root


def validate_branch_name(branch: str) -> str:
    """Validate branch name contains no shell metacharacters."""
    if re.search(r'[;&|$`\\\'\"(){}\[\]!#~]', branch):
        raise ValueError(f"Invalid branch name: {branch}")
    return branch


# ── Safe Command Runner ───────────────────────────────────


async def run_subprocess(args: list, cwd: str, timeout: int = 60, env: dict = None):
    """Run a command safely via subprocess_exec (no shell interpretation).

    Uses asyncio.create_subprocess_exec which passes args directly
    to the OS without shell interpretation, preventing injection.

    env: optional full environment dict for the subprocess. When provided,
    it REPLACES the inherited environment (create_subprocess_exec semantics),
    so callers must merge os.environ themselves (see _approve_mr). Used so a
    single glab call can authenticate as the OmniCheck bot via GITLAB_TOKEN
    without leaking the token into other calls or the parent process.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return _result(-1, "", "Command timed out")
    return _result(proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace"))


# Alias for the public API name used in tests and callers
run_exec = run_subprocess


def _result(returncode, stdout, stderr):
    """Create a simple result object."""
    class Result:
        pass
    r = Result()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ── Helper Functions ──────────────────────────────────────


def extract_changed_files(diff_text: str) -> list:
    """Extract file paths from unified diff output."""
    files = []
    for line in diff_text.split('\n'):
        if line.startswith('+++ b/'):
            path = line[6:]
            if path not in files:
                files.append(path)
    return files


def parse_commits(log_output: str) -> list:
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


def parse_diff_line_map(diff_text: str) -> dict:
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


def truncate_diff_if_needed(diff_text: str, line_count: int) -> tuple:
    """Truncate diff if it exceeds MAX_DIFF_LINES or MAX_DIFF_CHARS."""
    truncated = False
    reason = ""

    if line_count > MAX_DIFF_LINES:
        lines = diff_text.split('\n')[:MAX_DIFF_LINES]
        diff_text = '\n'.join(lines)
        truncated = True
        reason = f"{line_count} total lines, showing first {MAX_DIFF_LINES}"

    if len(diff_text) > MAX_DIFF_CHARS:
        original_chars = len(diff_text)
        cut_point = diff_text.rfind('\n', 0, MAX_DIFF_CHARS)
        if cut_point == -1:
            cut_point = MAX_DIFF_CHARS
        diff_text = diff_text[:cut_point]
        truncated = True
        char_reason = f"{len(diff_text)} of {original_chars} chars shown"
        reason = f"{reason}; {char_reason}" if reason else char_reason

    if truncated:
        diff_text += f"\n\n... [TRUNCATED: {reason}] ..."

    return diff_text, truncated


# ── Tool Implementations ──────────────────────────────────


async def _fetch_mr_data(mr_id: str, repo_root: str) -> dict:
    """Fetch all GitLab MR data in a single call."""
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    # Verify glab auth
    auth = await run_exec(["glab", "auth", "status"], cwd=repo_root)
    if auth.returncode != 0:
        return {
            "success": False,
            "error": "glab not authenticated. Run 'glab auth login'.",
            "error_type": "auth_failure",
        }

    # Fetch MR metadata (JSON)
    mr_json = await run_exec(
        ["glab", "mr", "view", mr_id, "-F", "json"], cwd=repo_root
    )
    if mr_json.returncode != 0:
        return {
            "success": False,
            "error": f"MR !{mr_id} not found.",
            "error_type": "mr_not_found",
        }
    metadata = json.loads(mr_json.stdout)

    # Fetch comments (default to empty on failure)
    comments_r = await run_exec(
        ["glab", "mr", "view", mr_id, "-c"], cwd=repo_root
    )
    comments = comments_r.stdout if comments_r.returncode == 0 else ""

    # Fetch diff (longer timeout)
    diff_r = await run_exec(
        ["glab", "mr", "diff", mr_id, "--raw"], cwd=repo_root, timeout=120
    )
    raw_diff = diff_r.stdout if diff_r.returncode == 0 else ""
    diff_lines = raw_diff.count('\n')

    # Extract branches
    source_branch = metadata.get("source_branch", "")
    target_branch = metadata.get("target_branch", "")

    # Fetch both branches and get commit list
    await run_exec(
        ["git", "fetch", "origin", source_branch, target_branch],
        cwd=repo_root,
        timeout=120,
    )
    commits_r = await run_exec(
        ["git", "log", "--oneline",
         f"origin/{target_branch}..origin/{source_branch}"],
        cwd=repo_root,
    )

    files_changed = extract_changed_files(raw_diff)
    diff_text, diff_truncated = truncate_diff_if_needed(raw_diff, diff_lines)
    diff_line_map = parse_diff_line_map(raw_diff)

    # Truncation guard (audit fix 5): when the cap cut the diff, name the
    # files whose regions did not survive, so threads anchored there can be
    # flagged needs-human with a stated reason instead of silently unseen.
    # A file counts as cut when its section lost hunks or added lines — the
    # hunk header alone surviving the cut is not completeness.
    truncated_files = []
    if diff_truncated:
        kept_map = parse_diff_line_map(diff_text)
        for path, info in diff_line_map.items():
            kept = kept_map.get(path)
            if (kept is None
                    or len(kept.get("hunks", [])) < len(info.get("hunks", []))
                    or len(kept.get("added_lines", [])) < len(info.get("added_lines", []))):
                truncated_files.append(path)

    return {
        "success": True,
        "mr_id": mr_id,
        "title": metadata.get("title", ""),
        "author": metadata.get("author", {}).get("username", ""),
        "source_branch": source_branch,
        "target_branch": target_branch,
        "pipeline_status": metadata.get("pipeline_status", "unknown"),
        "description": metadata.get("description", ""),
        "comments": comments,
        "diff": diff_text,
        "diff_line_count": diff_lines,
        "diff_too_large": diff_lines > MAX_DIFF_LINES,
        "diff_truncated": diff_truncated,
        "truncated_files": truncated_files,
        "diff_line_map": diff_line_map,
        "commits": parse_commits(commits_r.stdout),
        "files_changed": files_changed,
        "labels": metadata.get("labels", []),
        "head_sha": (metadata.get("diff_refs") or {}).get("head_sha", ""),
        "assignees": [a.get("username", "") for a in metadata.get("assignees", [])],
        "reviewers": [r.get("username", "") for r in metadata.get("reviewers", [])],
    }


async def _create_review_worktrees(mr_id: str, source_branch: str, repo_root: str) -> dict:
    """Create 3 isolated git worktrees for OmniForge agents."""
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
        source_branch = validate_branch_name(source_branch)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    # Resolve main repo root (handles worktrees)
    main_root = await _resolve_main_repo_root(repo_root)

    worktrees_dir = os.path.join(main_root, ".worktrees")
    os.makedirs(worktrees_dir, exist_ok=True)

    # Ensure .worktrees/ is in .gitignore
    gitignore_path = os.path.join(main_root, ".gitignore")
    result = await run_exec(["git", "check-ignore", "-q", ".worktrees"], cwd=main_root)
    if result.returncode != 0:
        existing = ""
        if os.path.exists(gitignore_path):
            with open(gitignore_path, "r") as f:
                existing = f.read()
        prefix = "" if existing.endswith("\n") or not existing else "\n"
        with open(gitignore_path, "a") as f:
            f.write(f"{prefix}.worktrees/\n")

    # Clean stale worktrees
    stale_count = 0
    for wt_type in WORKTREE_TYPES:
        name = f"omni-{wt_type}-{mr_id}"
        path = os.path.join(worktrees_dir, name)
        if os.path.exists(path):
            await run_exec(
                ["git", "worktree", "remove", path, "--force"],
                cwd=main_root, timeout=30,
            )
            stale_count += 1
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)

    await run_exec(["git", "worktree", "prune"], cwd=main_root)

    # Fetch source branch
    fetch = await run_exec(
        ["git", "fetch", "origin", source_branch],
        cwd=main_root, timeout=120,
    )
    if fetch.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to fetch branch: origin/{source_branch}",
            "error_type": "fetch_failed",
        }

    # Create 3 worktrees
    created = {}
    for wt_type in WORKTREE_TYPES:
        name = f"omni-{wt_type}-{mr_id}"
        path = os.path.join(worktrees_dir, name)
        r = await run_exec(
            ["git", "worktree", "add", path,
             f"origin/{source_branch}", "--detach"],
            cwd=main_root, timeout=30,
        )
        if r.returncode != 0:
            for _, cp in created.items():
                await run_exec(
                    ["git", "worktree", "remove", cp, "--force"],
                    cwd=main_root, timeout=30,
                )
            await run_exec(["git", "worktree", "prune"], cwd=main_root)
            return {
                "success": False,
                "error": f"Failed to create worktree '{name}': {r.stderr}",
                "error_type": "worktree_creation_failed",
                "partial_worktrees": created,
                "cleanup_performed": True,
            }
        created[wt_type] = os.path.abspath(path)

    return {
        "success": True,
        "mr_id": mr_id,
        "worktrees": created,
        "source_branch": source_branch,
        "stale_cleaned": stale_count,
    }


async def _cleanup_review_worktrees(mr_id: str, repo_root: str) -> dict:
    """Remove all OmniForge worktrees for a given MR."""
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    # Resolve main repo root (handles worktrees)
    main_root = await _resolve_main_repo_root(repo_root)

    worktrees_dir = os.path.join(main_root, ".worktrees")
    removed = []
    already_clean = []
    errors = []

    for wt_type in WORKTREE_TYPES:
        name = f"omni-{wt_type}-{mr_id}"
        path = os.path.join(worktrees_dir, name)

        if not os.path.exists(path):
            already_clean.append(name)
            continue

        await run_exec(
            ["git", "worktree", "remove", path, "--force"],
            cwd=main_root, timeout=30,
        )

        if os.path.exists(path):
            try:
                shutil.rmtree(path)
            except Exception as e:
                errors.append(f"Failed to remove {name}: {e}")
                continue

        removed.append(name)

    await run_exec(["git", "worktree", "prune"], cwd=main_root)

    return {
        "success": len(errors) == 0,
        "removed": removed,
        "already_clean": already_clean,
        "errors": errors,
    }


# ── Posting Tool Implementations ──────────────────────────


async def _get_mr_diff_refs(mr_id: str, repo_root: str) -> dict:
    """Fetch diff_refs (base_sha, head_sha, start_sha) for an MR."""
    r = await run_exec(
        ["glab", "mr", "view", mr_id, "-F", "json"], cwd=repo_root
    )
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to fetch MR !{mr_id} metadata: {r.stderr}",
            "error_type": "mr_not_found",
        }
    try:
        metadata = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": f"Failed to parse JSON from glab for MR !{mr_id}.",
            "error_type": "parse_error",
        }

    diff_refs = metadata.get("diff_refs", {})
    return {
        "success": True,
        "base_sha": diff_refs.get("base_sha", ""),
        "head_sha": diff_refs.get("head_sha", ""),
        "start_sha": diff_refs.get("start_sha", ""),
        "iid": str(metadata.get("iid", mr_id)),
        "labels": metadata.get("labels", []) or [],
        "state": metadata.get("state", ""),
    }


async def _post_review_summary(mr_id: str, summary: str, repo_root: str) -> dict:
    """Post a top-level summary comment on an MR."""
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    if not summary or not summary.strip():
        return {"success": False, "error": "Summary text is empty.", "error_type": "validation_error"}

    r = await run_exec(
        ["glab", "mr", "note", mr_id, "-m", summary],
        cwd=repo_root,
    )
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to post summary: {r.stderr}",
            "error_type": "post_failed",
        }

    return {"success": True, "mr_id": mr_id, "action": "summary_posted"}


async def _post_inline_thread_with_refs(
    mr_id: str,
    file_path: str,
    line_number: int,
    body: str,
    repo_root: str,
    diff_refs: dict,
) -> dict:
    """Post an inline discussion thread with pre-fetched diff refs.

    Args:
        diff_refs: Pre-fetched diff refs dict with 'iid', 'base_sha', 'head_sha', 'start_sha'.
    """
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    if not body or not body.strip():
        return {"success": False, "error": "Thread body is empty.", "error_type": "validation_error"}
    if line_number < 1:
        return {"success": False, "error": "line_number must be >= 1.", "error_type": "validation_error"}

    # glab api uses :fullpath placeholder which auto-resolves to the project path
    r = await run_exec(
        [
            "glab", "api",
            f"projects/:fullpath/merge_requests/{diff_refs['iid']}/discussions",
            "--method", "POST",
            "--raw-field", f"body={body}",
            "--raw-field", "position[position_type]=text",
            "--raw-field", f"position[base_sha]={diff_refs['base_sha']}",
            "--raw-field", f"position[head_sha]={diff_refs['head_sha']}",
            "--raw-field", f"position[start_sha]={diff_refs['start_sha']}",
            "--raw-field", f"position[new_path]={file_path}",
            "--raw-field", f"position[new_line]={line_number}",
        ],
        cwd=repo_root,
    )
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to post thread on {file_path}:{line_number}: {r.stderr}",
            "error_type": "post_failed",
        }

    return {
        "success": True,
        "mr_id": mr_id,
        "file": file_path,
        "line": line_number,
        "action": "inline_thread_posted",
    }


async def _post_inline_thread(
    mr_id: str,
    file_path: str,
    line_number: int,
    body: str,
    repo_root: str,
) -> dict:
    """Post an inline discussion thread on a specific diff line."""
    # Validate inputs first (before any I/O)
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    if not body or not body.strip():
        return {"success": False, "error": "Thread body is empty.", "error_type": "validation_error"}
    if line_number < 1:
        return {"success": False, "error": "line_number must be >= 1.", "error_type": "validation_error"}

    # Fetch diff refs (SHAs needed for position data)
    diff_refs = await _get_mr_diff_refs(mr_id, repo_root)
    if not diff_refs or not diff_refs.get("success"):
        return {
            "success": False,
            "error": diff_refs.get("error", f"Could not fetch diff refs for MR !{mr_id}.") if diff_refs else f"Could not fetch diff refs for MR !{mr_id}.",
            "error_type": diff_refs.get("error_type", "mr_not_found") if diff_refs else "mr_not_found",
        }

    return await _post_inline_thread_with_refs(
        mr_id, file_path, line_number, body, repo_root, diff_refs
    )


async def _post_full_review(
    mr_id: str,
    summary: str,
    findings: list,
    repo_root: str,
) -> dict:
    """Post a full review: summary comment + inline threads for each finding.

    findings: list of dicts with file_path (str), line_number (int), body (str).
    """
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    results = {"summary": None, "threads": [], "errors": []}

    # Fetch diff refs once for all inline threads (avoids N+1 API calls)
    diff_refs = await _get_mr_diff_refs(mr_id, repo_root)
    if not diff_refs or not diff_refs.get("success"):
        return {
            "success": False,
            "mr_id": mr_id,
            "summary_posted": False,
            "threads_posted": 0,
            "threads_total": len(findings),
            "errors": [diff_refs.get("error", f"Could not fetch diff refs for MR !{mr_id}.") if diff_refs else f"Could not fetch diff refs for MR !{mr_id}."],
        }

    # Post summary
    summary_result = await _post_review_summary(mr_id, summary, repo_root)
    results["summary"] = summary_result
    if not summary_result["success"]:
        results["errors"].append(f"Summary: {summary_result['error']}")

    # Post each finding as an inline thread (reusing pre-fetched refs)
    for i, finding in enumerate(findings):
        fp = finding.get("file_path", "")
        ln = finding.get("line_number", 0)
        body = finding.get("body", "")

        if not fp or not body or ln < 1:
            results["errors"].append(
                f"Finding {i+1}: missing file_path, line_number, or body"
            )
            results["threads"].append({
                "success": False, "index": i + 1, "error": "invalid finding",
            })
            continue

        thread_result = await _post_inline_thread_with_refs(
            mr_id, fp, ln, body, repo_root, diff_refs
        )
        results["threads"].append({
            "success": thread_result["success"],
            "index": i + 1,
            "file": fp,
            "line": ln,
            "error": thread_result.get("error", ""),
        })
        if not thread_result["success"]:
            results["errors"].append(
                f"Finding {i+1} ({fp}:{ln}): {thread_result['error']}"
            )

    posted = sum(1 for t in results["threads"] if t["success"])

    return {
        "success": len(results["errors"]) == 0,
        "mr_id": mr_id,
        "summary_posted": results["summary"]["success"] if results["summary"] else False,
        "threads_posted": posted,
        "threads_total": len(findings),
        "errors": results["errors"],
    }


# ── Issue Creation ────────────────────────────────────────


async def _create_linked_issue(
    mr_id: str,
    title: str,
    description: str,
    labels: str,
    repo_root: str,
) -> dict:
    """Create a GitLab issue linked to an MR via --linked-mr flag."""
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    if not title or not title.strip():
        return {
            "success": False,
            "error": "Issue title is empty.",
            "error_type": "validation_error",
        }

    args = [
        "glab", "issue", "create",
        "--title", title,
        "--description", description or "",
        "--linked-mr", mr_id,
        "--no-editor",
    ]
    if labels and labels.strip():
        args.extend(["--label", labels])

    r = await run_exec(args, cwd=repo_root)
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to create issue: {r.stderr}",
            "error_type": "issue_creation_failed",
        }

    # Parse issue URL from glab output
    issue_url = ""
    for line in r.stdout.strip().split("\n"):
        for word in line.split():
            if word.startswith("http"):
                issue_url = word
                break
        if issue_url:
            break

    return {
        "success": True,
        "mr_id": mr_id,
        "issue_url": issue_url,
        "output": r.stdout.strip(),
        "action": "issue_created",
    }


# ── Discussion Tools ─────────────────────────────────────


async def _fetch_mr_discussions(mr_id: str, repo_root: str) -> dict:
    """Fetch all discussion threads from an MR with structured data."""
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    # Get MR IID
    diff_refs = await _get_mr_diff_refs(mr_id, repo_root)
    if not diff_refs or not diff_refs.get("success"):
        return {
            "success": False,
            "error": diff_refs.get("error", f"Could not fetch MR !{mr_id}.") if diff_refs else f"Could not fetch MR !{mr_id}.",
            "error_type": diff_refs.get("error_type", "mr_not_found") if diff_refs else "mr_not_found",
        }
    iid = diff_refs["iid"]

    # Fetch all discussions (paginated)
    r = await run_exec(
        ["glab", "api", f"projects/:fullpath/merge_requests/{iid}/discussions", "--paginate"],
        cwd=repo_root, timeout=120,
    )
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to fetch discussions: {r.stderr}",
            "error_type": "api_error",
        }

    try:
        raw_discussions = json.loads(r.stdout) if r.stdout.strip() else []
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "Failed to parse discussions JSON.",
            "error_type": "parse_error",
        }

    # Parse into structured format
    discussions = []
    for disc in raw_discussions:
        notes = [n for n in disc.get("notes", []) if not n.get("system", False)]
        if not notes:
            continue
        first_note = notes[0]
        position = first_note.get("position") or {}
        note_type = first_note.get("type", "")

        # Determine if this is an inline discussion:
        # 1. Primary: position data has new_path (most reliable)
        # 2. Fallback: note type is "DiffNote" (GitLab's type for inline comments)
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
            # Old-side anchor + the sha the thread was positioned at, so
            # re-anchoring through a later delta is arithmetic instead of
            # guesswork (audit fix 4). Old-side threads carry only
            # old_path/old_line; anchor_sha falls back to "" for generals.
            "old_path": position.get("old_path"),
            "old_line": position.get("old_line"),
            "anchor_sha": position.get("head_sha") or "",
            "body": first_note.get("body", ""),
            "author": first_note.get("author", {}).get("username", ""),
            "created_at": first_note.get("created_at", ""),
            "replies": [
                {
                    "author": n.get("author", {}).get("username", ""),
                    "body": n.get("body", ""),
                    "created_at": n.get("created_at", ""),
                }
                for n in notes[1:]
            ],
        })

    unresolved = sum(1 for d in discussions if d["resolvable"] and not d["resolved"])
    resolved = sum(1 for d in discussions if d["resolvable"] and d["resolved"])

    return {
        "success": True,
        "mr_id": mr_id,
        "discussions": discussions,
        "total": len(discussions),
        "unresolved": unresolved,
        "resolved": resolved,
    }


async def _reply_to_discussion(
    mr_id: str, discussion_id: str, body: str, repo_root: str
) -> dict:
    """Post a reply to a specific discussion thread."""
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    if not body or not body.strip():
        return {"success": False, "error": "Reply body is empty.", "error_type": "validation_error"}

    diff_refs = await _get_mr_diff_refs(mr_id, repo_root)
    if not diff_refs or not diff_refs.get("success"):
        return {
            "success": False,
            "error": diff_refs.get("error", f"Could not fetch MR !{mr_id}.") if diff_refs else f"Could not fetch MR !{mr_id}.",
            "error_type": diff_refs.get("error_type", "mr_not_found") if diff_refs else "mr_not_found",
        }
    iid = diff_refs["iid"]

    r = await run_exec(
        [
            "glab", "api",
            f"projects/:fullpath/merge_requests/{iid}/discussions/{discussion_id}/notes",
            "--method", "POST",
            "--raw-field", f"body={body}",
        ],
        cwd=repo_root,
    )
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to reply: {r.stderr}",
            "error_type": "post_failed",
        }

    return {
        "success": True,
        "mr_id": mr_id,
        "discussion_id": discussion_id,
        "action": "reply_posted",
    }


async def _resolve_discussion(
    mr_id: str, discussion_id: str, resolved: bool, repo_root: str
) -> dict:
    """Resolve or unresolve a discussion thread."""
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    diff_refs = await _get_mr_diff_refs(mr_id, repo_root)
    if not diff_refs or not diff_refs.get("success"):
        return {
            "success": False,
            "error": diff_refs.get("error", f"Could not fetch MR !{mr_id}.") if diff_refs else f"Could not fetch MR !{mr_id}.",
            "error_type": diff_refs.get("error_type", "mr_not_found") if diff_refs else "mr_not_found",
        }
    iid = diff_refs["iid"]

    resolved_str = "true" if resolved else "false"
    r = await run_exec(
        [
            "glab", "api",
            f"projects/:fullpath/merge_requests/{iid}/discussions/{discussion_id}",
            "--method", "PUT",
            "--raw-field", f"resolved={resolved_str}",
        ],
        cwd=repo_root,
    )
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to {'resolve' if resolved else 'unresolve'} discussion: {r.stderr}",
            "error_type": "resolve_failed",
        }

    return {
        "success": True,
        "mr_id": mr_id,
        "discussion_id": discussion_id,
        "resolved": resolved,
        "action": "discussion_resolved" if resolved else "discussion_unresolved",
    }


# ── S1 Screenshot Flow Helpers ────────────────────

# GitLab project uploads platform limit (docs.gitlab.com): 100 MiB.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# Fixed multipart boundary for upload bodies: deterministic so the body and
# the Content-Type header can be verified byte-for-byte. build_multipart_
# upload_body refuses file content containing it (would corrupt the part
# framing).
MULTIPART_BOUNDARY = "omniforge-upload-3f2a8c1d"


def multipart_content_type() -> str:
    """The Content-Type header matching MULTIPART_BOUNDARY."""
    return f"multipart/form-data; boundary={MULTIPART_BOUNDARY}"


def build_multipart_upload_body(file_path: str) -> bytes:
    """Build the multipart/form-data body for POST /projects/:id/uploads.

    The installed glab (1.6x) has no --form flag and serializes --field
    values as JSON strings, so multipart cannot be expressed through it
    directly. The body is built here and sent raw via `glab api --input`
    with an explicit Content-Type header carrying the same boundary.
    """
    filename = os.path.basename(file_path)
    if not filename or re.search(r'[\x00-\x1f\x7f"]', filename):
        # the name lands inside a MIME header; quotes/newlines would break
        # part framing (header injection into the request body)
        raise ValueError(
            "file name contains quotes or control characters; refusing to "
            "build the multipart body")
    with open(file_path, "rb") as fh:
        content = fh.read()
    if MULTIPART_BOUNDARY.encode() in content:
        raise ValueError(
            "file content contains the multipart boundary; refusing to "
            "build an ambiguous body")
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    head = (
        f"--{MULTIPART_BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    tail = f"\r\n--{MULTIPART_BOUNDARY}--\r\n".encode()
    return head + content + tail


async def _update_mr_labels(mr_id: str, add_labels: str = "",
                            remove_labels: str = "", repo_root: str = "") -> dict:
    """Add and/or remove MR labels in ONE PUT.

    Uses the merge request API's add_labels/remove_labels params directly
    (auto-create semantics, no label pre-creation, no GET-merge-PUT race).
    """
    def _normalize_labels(labels, arg_name):
        """Accept a comma-separated string OR the list a decision plan
        emits; anything else is a validation error, never a TypeError."""
        if labels is None:
            return ""
        if isinstance(labels, str):
            return validate_labels(labels)
        if isinstance(labels, (list, tuple)):
            for item in labels:
                if not isinstance(item, str):
                    raise ValueError(
                        f"{arg_name} list items must be strings; got: "
                        f"{item!r}")
            return validate_labels(",".join(labels))
        raise ValueError(
            f"{arg_name} must be a comma-separated string or a list of "
            f"strings; got: {type(labels).__name__}")

    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
        add_labels = _normalize_labels(add_labels, "add_labels")
        remove_labels = _normalize_labels(remove_labels, "remove_labels")
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    if not add_labels and not remove_labels:
        return {
            "success": False,
            "error": "No labels given: pass add_labels and/or remove_labels.",
            "error_type": "validation_error",
        }

    diff_refs = await _get_mr_diff_refs(mr_id, repo_root)
    if not diff_refs or not diff_refs.get("success"):
        return {
            "success": False,
            "error": diff_refs.get("error", f"Could not fetch MR !{mr_id}.") if diff_refs else f"Could not fetch MR !{mr_id}.",
            "error_type": diff_refs.get("error_type", "mr_not_found") if diff_refs else "mr_not_found",
        }
    iid = diff_refs["iid"]

    args = [
        "glab", "api", f"projects/:fullpath/merge_requests/{iid}",
        "--method", "PUT",
    ]
    if add_labels:
        args.extend(["--raw-field", f"add_labels={add_labels}"])
    if remove_labels:
        args.extend(["--raw-field", f"remove_labels={remove_labels}"])

    r = await run_exec(args, cwd=repo_root)
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to update labels: {r.stderr}",
            "error_type": "label_update_failed",
        }
    try:
        updated = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "Failed to parse MR response after label update.",
            "error_type": "parse_error",
        }

    return {
        "success": True,
        "mr_id": mr_id,
        "iid": iid,
        "labels": updated.get("labels", []),
        "added": add_labels,
        "removed": remove_labels,
        "action": "labels_updated",
    }


async def _post_mr_note(mr_id: str, body: str, repo_root: str) -> dict:
    """Post a top-level MR note via the API, returning note + discussion ids.

    The returned discussion_id is the durable thread identity the S1
    screenshot reply detection matches author replies against.
    """
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    if not body or not body.strip():
        return {"success": False, "error": "Note body is empty.", "error_type": "validation_error"}

    diff_refs = await _get_mr_diff_refs(mr_id, repo_root)
    if not diff_refs or not diff_refs.get("success"):
        return {
            "success": False,
            "error": diff_refs.get("error", f"Could not fetch MR !{mr_id}.") if diff_refs else f"Could not fetch MR !{mr_id}.",
            "error_type": diff_refs.get("error_type", "mr_not_found") if diff_refs else "mr_not_found",
        }
    iid = diff_refs["iid"]

    r = await run_exec(
        [
            "glab", "api",
            f"projects/:fullpath/merge_requests/{iid}/notes",
            "--method", "POST",
            "--raw-field", f"body={body}",
        ],
        cwd=repo_root,
    )
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to post note: {r.stderr}",
            "error_type": "post_failed",
        }
    try:
        note = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "Failed to parse note response.",
            "error_type": "parse_error",
        }

    return {
        "success": True,
        "mr_id": mr_id,
        "note_id": note.get("id"),
        "discussion_id": note.get("discussion_id", ""),
        "action": "note_posted",
    }


async def _upload_project_file(file_path: str, repo_root: str) -> dict:
    """Upload a file to the project via POST /projects/:id/uploads.

    Returns the response markdown (embeddable verbatim in notes), url and
    full_url. Multipart is built locally (glab 1.6x cannot express it) and
    sent raw via `glab api --input` with the matching Content-Type header.
    The body file is temporary and always removed.
    """
    try:
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    if not file_path or not os.path.isabs(file_path):
        return {
            "success": False,
            "error": f"file_path must be absolute: {file_path}",
            "error_type": "validation_error",
        }
    if not os.path.isfile(file_path):
        return {
            "success": False,
            "error": f"File not found: {file_path}",
            "error_type": "validation_error",
        }
    if os.path.getsize(file_path) > MAX_UPLOAD_BYTES:
        return {
            "success": False,
            "error": (
                f"File exceeds the {MAX_UPLOAD_BYTES} byte GitLab uploads "
                f"limit: {file_path}"
            ),
            "error_type": "validation_error",
        }
    try:
        body = build_multipart_upload_body(file_path)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
                prefix="omni-upload-", suffix=".multipart", delete=False) as tmp:
            tmp_name = tmp.name  # captured first: a failed write must not leak
            tmp.write(body)
        r = await run_exec(
            [
                "glab", "api", "projects/:fullpath/uploads",
                "--method", "POST",
                "--input", tmp_name,
                "-H", multipart_content_type(),
            ],
            cwd=repo_root, timeout=300,
        )
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)

    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to upload file: {r.stderr}",
            "error_type": "upload_failed",
        }
    try:
        payload = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "Failed to parse upload response.",
            "error_type": "parse_error",
        }

    return {
        "success": True,
        "markdown": payload.get("markdown", ""),
        "url": payload.get("url", ""),
        "full_url": payload.get("full_url", ""),
        "action": "file_uploaded",
    }


# ── OmniFix Cleanup ─────────────────────────────────────


async def _cleanup_omnifix_worktrees(mr_id: str, repo_root: str) -> dict:
    """Remove all OmniFix worktrees and temp branches for a given MR."""
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    # Resolve main repo root (handles worktrees)
    main_root = await _resolve_main_repo_root(repo_root)

    worktrees_dir = os.path.join(main_root, ".worktrees")
    removed = []
    already_clean = []
    errors = []

    # 1. Remove fix worktree: .worktrees/omnifix-{mr_id}
    fix_name = f"omnifix-{mr_id}"
    fix_path = os.path.join(worktrees_dir, fix_name)
    if os.path.exists(fix_path):
        await run_exec(
            ["git", "worktree", "remove", fix_path, "--force"],
            cwd=main_root, timeout=30,
        )
        if os.path.exists(fix_path):
            try:
                shutil.rmtree(fix_path)
            except Exception as e:
                errors.append(f"Failed to remove {fix_name}: {e}")
        if not os.path.exists(fix_path):
            removed.append(fix_name)
    else:
        already_clean.append(fix_name)

    # 2. Remove triage worktrees: .worktrees/omnifix-triage-{mr_id}-*
    if os.path.isdir(worktrees_dir):
        import glob
        triage_pattern = os.path.join(worktrees_dir, f"omnifix-triage-{mr_id}-*")
        for triage_path in glob.glob(triage_pattern):
            triage_name = os.path.basename(triage_path)
            await run_exec(
                ["git", "worktree", "remove", triage_path, "--force"],
                cwd=main_root, timeout=30,
            )
            if os.path.exists(triage_path):
                try:
                    shutil.rmtree(triage_path)
                except Exception as e:
                    errors.append(f"Failed to remove {triage_name}: {e}")
            if not os.path.exists(triage_path):
                removed.append(triage_name)

    # 3. Delete temp branch (ignore error if doesn't exist)
    await run_exec(
        ["git", "branch", "-D", f"omnifix-temp-{mr_id}"],
        cwd=main_root, timeout=10,
    )

    # 4. Prune
    await run_exec(["git", "worktree", "prune"], cwd=main_root)

    return {
        "success": len(errors) == 0,
        "mr_id": mr_id,
        "removed": removed,
        "already_clean": already_clean,
        "errors": errors,
    }


# ── MR Approval ─────────────────────────────────────────


def _validate_sha(sha: str) -> str:
    """Validate a git SHA is hexadecimal (blocks injection even though args are exec-safe)."""
    if not sha:
        return ""
    if not re.match(r'^[0-9a-fA-F]+$', sha):
        raise ValueError(f"Invalid SHA: {sha}. Must be hexadecimal.")
    return sha


async def _approve_mr(mr_id: str, repo_root: str, sha: str = "",
                      checked_sha: str = "") -> dict:
    """Approve a GitLab merge request.

    When the OMNICHECK_BOT_TOKEN environment variable is set, the single approval
    call authenticates as the bot (via GITLAB_TOKEN) so it can approve MRs whose
    creator/committer approval settings block the current glab user. Otherwise it
    approves as the current glab user. The approval is ALWAYS pinned to a commit
    sha: the caller-provided sha if given, else the MR's resolved HEAD sha. If
    neither is available the call is refused — an unpinned approval would not go
    stale on new commits, contradicting the tool's contract.

    Guards (audit fix 2, fail-closed — a guard that cannot run also refuses):
    - unresolved resolvable threads exist -> refuse (unresolved_threads)
    - the MR lacks the omniforge::reviewed label -> refuse
      (missing_reviewed_label): the bot approval must never be the thing that
      certifies an MR OmniForge did not review
    - checked_sha (the head the verification ran against) differs from the
      live head -> refuse (head_moved)
    """
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
        sha = _validate_sha(sha)
        checked_sha = _validate_sha(checked_sha)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    # Resolve IID + HEAD sha
    diff_refs = await _get_mr_diff_refs(mr_id, repo_root)
    if not diff_refs or not diff_refs.get("success"):
        return {
            "success": False,
            "error": diff_refs.get("error", f"Could not fetch MR !{mr_id}.") if diff_refs else f"Could not fetch MR !{mr_id}.",
            "error_type": diff_refs.get("error_type", "mr_not_found") if diff_refs else "mr_not_found",
        }
    iid = diff_refs["iid"]
    head_sha = sha if sha else diff_refs.get("head_sha", "")

    # Guard: never approve unpinned. Without a sha the approval would not be
    # invalidated by new commits, silently weakening the gate.
    if not head_sha:
        return {
            "success": False,
            "error": (
                f"Cannot pin approval for MR !{mr_id}: no HEAD sha resolved and none "
                f"provided. Re-fetch the MR or pass an explicit sha."
            ),
            "error_type": "no_head_sha",
        }

    # Guard: the bot approval may only certify an MR OmniForge reviewed.
    labels = diff_refs.get("labels", []) or []
    if "omniforge::reviewed" not in labels:
        return {
            "success": False,
            "error": (
                f"Refusing to approve MR !{mr_id}: the omniforge::reviewed label "
                f"is missing. Run the OmniForge review first; this approval must "
                f"never be what certifies an unreviewed MR."
            ),
            "error_type": "missing_reviewed_label",
        }

    # Guard: no approval past unresolved resolvable threads.
    disc_r = await run_exec(
        ["glab", "api", f"projects/:fullpath/merge_requests/{iid}/discussions",
         "--paginate"],
        cwd=repo_root, timeout=120,
    )
    if disc_r.returncode != 0:
        return {
            "success": False,
            "error": (
                f"Refusing to approve MR !{mr_id}: could not verify thread "
                f"state ({disc_r.stderr.strip()[:200]})."
            ),
            "error_type": "guard_check_failed",
        }
    try:
        raw_discussions = json.loads(disc_r.stdout) if disc_r.stdout.strip() else []
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": f"Refusing to approve MR !{mr_id}: could not parse thread state.",
            "error_type": "guard_check_failed",
        }
    unresolved = sum(
        1 for d in raw_discussions
        if d.get("resolvable") and not d.get("resolved")
    )
    if unresolved:
        return {
            "success": False,
            "error": (
                f"Refusing to approve MR !{mr_id}: {unresolved} unresolved "
                f"resolvable thread(s) remain. Resolve or address them first."
            ),
            "error_type": "unresolved_threads",
            "unresolved_count": unresolved,
        }

    # Guard: the approval must be for the head the verification saw.
    if checked_sha and checked_sha != head_sha:
        return {
            "success": False,
            "error": (
                f"Refusing to approve MR !{mr_id}: head moved since the check "
                f"(checked {checked_sha[:12]}, live {str(head_sha)[:12]}). "
                f"Re-run the verification at the new head."
            ),
            "error_type": "head_moved",
        }

    # Determine approver: bot when its token is configured, else current glab user
    bot_token = os.environ.get(OMNICHECK_BOT_TOKEN_ENV, "")
    used_bot = bool(bot_token)

    args = [
        "glab", "api",
        f"projects/:fullpath/merge_requests/{iid}/approve",
        "--method", "POST",
        "--raw-field", f"sha={head_sha}",
    ]

    # Run the one approval call as the bot when configured. env fully replaces the
    # inherited environment, so we merge os.environ and override GITLAB_TOKEN.
    if used_bot:
        env = {**os.environ, "GITLAB_TOKEN": bot_token}
        r = await run_exec(args, cwd=repo_root, env=env)
    else:
        r = await run_exec(args, cwd=repo_root)

    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to approve MR !{mr_id}: {r.stderr}",
            "error_type": "approve_failed",
            "approver": "bot" if used_bot else "current_user",
        }

    return {
        "success": True,
        "mr_id": mr_id,
        "iid": iid,
        "sha": head_sha,
        "approver": "bot" if used_bot else "current_user",
        "action": "mr_approved",
    }


# ── GitHub Platform Detection & Helpers ───────────────────


async def _detect_platform(repo_root: str) -> str:
    """Detect whether the repo uses GitLab or GitHub.

    Returns ``'gitlab'``, ``'github'``, or ``'unknown'``.

    Checks ``git remote -v`` for github.com or gitlab.com patterns first,
    then falls back to checking which CLI tool is authenticated.
    """
    r = await run_exec(["git", "remote", "-v"], cwd=repo_root, timeout=10)
    if r.returncode == 0:
        remote_url = r.stdout.lower()
        if "github.com" in remote_url:
            return "github"
        if "gitlab.com" in remote_url:
            return "gitlab"
        # Check for self-hosted GitLab (common patterns)
        if "gitlab" in remote_url:
            return "gitlab"

    # Fallback: check which CLI is available
    gh_check = await run_exec(["gh", "auth", "status"], cwd=repo_root, timeout=10)
    if gh_check.returncode == 0:
        return "github"
    glab_check = await run_exec(["glab", "auth", "status"], cwd=repo_root, timeout=10)
    if glab_check.returncode == 0:
        return "gitlab"

    return "unknown"


async def _get_github_repo_id(repo_root: str) -> str:
    """Get the ``owner/repo`` identifier for GitHub API calls."""
    r = await run_exec(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=repo_root, timeout=10,
    )
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


async def _get_pr_head_sha(pr_id: str, repo_root: str) -> str:
    """Get the HEAD SHA of a GitHub PR for inline comment positioning."""
    r = await run_exec(
        ["gh", "pr", "view", pr_id, "--json", "headRefOid", "-q", ".headRefOid"],
        cwd=repo_root, timeout=10,
    )
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


# ── GitHub Tool Implementations ───────────────────────────


def validate_pr_id(pr_id: str) -> str:
    """Strip leading '#' and validate pr_id is numeric."""
    pr_id = pr_id.lstrip('#')
    if not re.match(r'^\d+$', pr_id):
        raise ValueError(f"Invalid PR ID: {pr_id}. Must be numeric.")
    return pr_id


async def _fetch_pr_data(pr_id: str, repo_root: str) -> dict:
    """Fetch all GitHub PR data (metadata, comments, diff, commits) in one call."""
    try:
        pr_id = validate_pr_id(pr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    # Verify gh auth
    auth = await run_exec(["gh", "auth", "status"], cwd=repo_root)
    if auth.returncode != 0:
        return {
            "success": False,
            "error": "gh not authenticated. Run 'gh auth login'.",
            "error_type": "auth_failure",
        }

    # Fetch PR metadata (JSON)
    pr_json = await run_exec(
        [
            "gh", "pr", "view", pr_id, "--json",
            "title,body,headRefName,baseRefName,state,labels,assignees,"
            "reviewRequests,comments,author,headRefOid",
        ],
        cwd=repo_root,
    )
    if pr_json.returncode != 0:
        return {
            "success": False,
            "error": f"PR #{pr_id} not found.",
            "error_type": "pr_not_found",
        }

    try:
        metadata = json.loads(pr_json.stdout)
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": f"Failed to parse PR #{pr_id} metadata.",
            "error_type": "parse_error",
        }

    # Fetch diff (longer timeout)
    diff_r = await run_exec(
        ["gh", "pr", "diff", pr_id], cwd=repo_root, timeout=120
    )
    raw_diff = diff_r.stdout if diff_r.returncode == 0 else ""
    diff_lines = raw_diff.count('\n')

    # Extract branch names from GitHub JSON fields
    source_branch = metadata.get("headRefName", "")
    target_branch = metadata.get("baseRefName", "")

    # Fetch commit list via gh pr view --json commits
    commits_r = await run_exec(
        [
            "gh", "pr", "view", pr_id, "--json", "commits",
            "-q", '.commits[] | "\(.oid[:7]) \(.messageHeadline)"',
        ],
        cwd=repo_root, timeout=30,
    )
    commits_text = commits_r.stdout if commits_r.returncode == 0 else ""

    # Build comments text from metadata
    comments_list = metadata.get("comments", [])
    comments_parts = []
    for c in comments_list:
        author = c.get("author", {}).get("login", "unknown")
        body = c.get("body", "")
        comments_parts.append(f"**{author}**:\n{body}")
    comments = "\n\n---\n\n".join(comments_parts)

    files_changed = extract_changed_files(raw_diff)
    diff_text, diff_truncated = truncate_diff_if_needed(raw_diff, diff_lines)
    diff_line_map = parse_diff_line_map(raw_diff)

    # Truncation guard, GitHub parity with _fetch_mr_data (audit fix 5).
    truncated_files = []
    if diff_truncated:
        kept_map = parse_diff_line_map(diff_text)
        for path, info in diff_line_map.items():
            kept = kept_map.get(path)
            if (kept is None
                    or len(kept.get("hunks", [])) < len(info.get("hunks", []))
                    or len(kept.get("added_lines", [])) < len(info.get("added_lines", []))):
                truncated_files.append(path)

    return {
        "success": True,
        "pr_id": pr_id,
        "title": metadata.get("title", ""),
        "author": metadata.get("author", {}).get("login", ""),
        "source_branch": source_branch,
        "target_branch": target_branch,
        "state": metadata.get("state", ""),
        "description": metadata.get("body", "") or "",
        "comments": comments,
        "diff": diff_text,
        "diff_line_count": diff_lines,
        "diff_too_large": diff_lines > MAX_DIFF_LINES,
        "diff_truncated": diff_truncated,
        "truncated_files": truncated_files,
        "diff_line_map": diff_line_map,
        "commits": parse_commits(commits_text),
        "files_changed": files_changed,
        "labels": [l.get("name", "") for l in metadata.get("labels", [])],
        "head_sha": metadata.get("headRefOid", "") or "",
        "assignees": [a.get("login", "") for a in metadata.get("assignees", [])],
        "reviewers": [r.get("login", "") for r in metadata.get("reviewRequests", [])],
    }


async def _post_pr_review_summary(pr_id: str, summary: str, repo_root: str) -> dict:
    """Post a top-level summary comment on a GitHub PR."""
    try:
        pr_id = validate_pr_id(pr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    if not summary or not summary.strip():
        return {"success": False, "error": "Summary text is empty.", "error_type": "validation_error"}

    r = await run_exec(
        ["gh", "pr", "comment", pr_id, "--body", summary],
        cwd=repo_root,
    )
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to post summary: {r.stderr}",
            "error_type": "post_failed",
        }

    return {"success": True, "pr_id": pr_id, "action": "summary_posted"}


async def _post_pr_inline_thread(
    pr_id: str,
    file_path: str,
    line_number: int,
    body: str,
    repo_root: str,
) -> dict:
    """Post an inline review comment on a GitHub PR diff line."""
    try:
        pr_id = validate_pr_id(pr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    if not body or not body.strip():
        return {"success": False, "error": "Thread body is empty.", "error_type": "validation_error"}
    if line_number < 1:
        return {"success": False, "error": "line_number must be >= 1.", "error_type": "validation_error"}

    # Get owner/repo
    repo_id = await _get_github_repo_id(repo_root)
    if not repo_id:
        return {
            "success": False,
            "error": "Could not determine GitHub repo (owner/repo). Run 'gh repo set-default'.",
            "error_type": "repo_not_found",
        }

    # Get PR head SHA for commit_id
    head_sha = await _get_pr_head_sha(pr_id, repo_root)
    if not head_sha:
        return {
            "success": False,
            "error": f"Could not fetch HEAD SHA for PR #{pr_id}.",
            "error_type": "pr_not_found",
        }

    r = await run_exec(
        [
            "gh", "api",
            f"repos/{repo_id}/pulls/{pr_id}/comments",
            "--method", "POST",
            "--field", f"body={body}",
            "--field", f"commit_id={head_sha}",
            "--field", f"path={file_path}",
            "--field", f"line={line_number}",
            "--field", "side=RIGHT",
        ],
        cwd=repo_root,
    )
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to post thread on {file_path}:{line_number}: {r.stderr}",
            "error_type": "post_failed",
        }

    return {
        "success": True,
        "pr_id": pr_id,
        "file": file_path,
        "line": line_number,
        "action": "inline_thread_posted",
    }


async def _post_pr_full_review(
    pr_id: str,
    summary: str,
    findings: list,
    repo_root: str,
) -> dict:
    """Post a full GitHub PR review: summary comment + inline threads for each finding.

    findings: list of dicts with file_path (str), line_number (int), body (str).
    """
    try:
        pr_id = validate_pr_id(pr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    results = {"summary": None, "threads": [], "errors": []}

    # Post summary
    summary_result = await _post_pr_review_summary(pr_id, summary, repo_root)
    results["summary"] = summary_result
    if not summary_result["success"]:
        results["errors"].append(f"Summary: {summary_result['error']}")

    # Post each finding as an inline thread
    for i, finding in enumerate(findings):
        fp = finding.get("file_path", "")
        ln = finding.get("line_number", 0)
        body = finding.get("body", "")

        if not fp or not body or ln < 1:
            results["errors"].append(
                f"Finding {i+1}: missing file_path, line_number, or body"
            )
            results["threads"].append({
                "success": False, "index": i + 1, "error": "invalid finding",
            })
            continue

        thread_result = await _post_pr_inline_thread(pr_id, fp, ln, body, repo_root)
        results["threads"].append({
            "success": thread_result["success"],
            "index": i + 1,
            "file": fp,
            "line": ln,
            "error": thread_result.get("error", ""),
        })
        if not thread_result["success"]:
            results["errors"].append(
                f"Finding {i+1} ({fp}:{ln}): {thread_result['error']}"
            )

    posted = sum(1 for t in results["threads"] if t["success"])

    return {
        "success": len(results["errors"]) == 0,
        "pr_id": pr_id,
        "summary_posted": results["summary"]["success"] if results["summary"] else False,
        "threads_posted": posted,
        "threads_total": len(findings),
        "errors": results["errors"],
    }


async def _create_github_pr(
    repo_root: str,
    title: str = None,
    description: str = None,
    target_branch: str = "main",
    source_branch: str = None,
    assignees: str = None,
    reviewers: str = None,
    labels: str = None,
    draft: bool = False,
    web: bool = False,
) -> dict:
    """Create a GitHub pull request using gh CLI safely."""
    try:
        repo_root = validate_repo_root(repo_root)
        if target_branch:
            target_branch = validate_target_branch(target_branch)
        if source_branch:
            source_branch = validate_branch_name(source_branch)
        if title:
            title = validate_title(title)
        if labels:
            labels = validate_labels(labels)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    # Build the command args safely (list of strings, no shell interpretation)
    args = ["gh", "pr", "create"]

    if target_branch:
        args.extend(["--base", target_branch])
    if source_branch:
        args.extend(["--head", source_branch])
    if title:
        args.extend(["--title", title])
    if description:
        args.extend(["--body", description])
    if assignees:
        args.extend(["--assignee", assignees])
    if reviewers:
        args.extend(["--reviewer", reviewers])
    if labels:
        args.extend(["--label", labels])
    if draft:
        args.append("--draft")
    if web:
        args.append("--web")

    r = await run_exec(args, cwd=repo_root, timeout=120)

    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to create PR: {r.stderr}",
            "error_type": "pr_creation_failed",
        }

    # Parse PR URL from output
    pr_url = ""
    for line in r.stdout.strip().split("\n"):
        for word in line.split():
            if word.startswith("http"):
                pr_url = word
                break
        if pr_url:
            break

    return {
        "success": True,
        "pr_url": pr_url,
        "output": r.stdout.strip(),
        "action": "pr_created",
    }


async def _fetch_pr_discussions(pr_id: str, repo_root: str) -> dict:
    """Fetch all review comments (inline threads) from a GitHub PR."""
    try:
        pr_id = validate_pr_id(pr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    # Get owner/repo
    repo_id = await _get_github_repo_id(repo_root)
    if not repo_id:
        return {
            "success": False,
            "error": "Could not determine GitHub repo (owner/repo). Run 'gh repo set-default'.",
            "error_type": "repo_not_found",
        }

    r = await run_exec(
        ["gh", "api", f"repos/{repo_id}/pulls/{pr_id}/comments", "--paginate"],
        cwd=repo_root, timeout=120,
    )
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to fetch discussions: {r.stderr}",
            "error_type": "api_error",
        }

    try:
        raw_comments = json.loads(r.stdout) if r.stdout.strip() else []
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "Failed to parse discussions JSON.",
            "error_type": "parse_error",
        }

    # GitHub uses flat comments with in_reply_to. Group them into threads.
    # Top-level comments have no in_reply_to_id; replies reference their parent.
    threads_by_id = {}
    replies = []

    for comment in raw_comments:
        reply_to = comment.get("in_reply_to_id")
        if reply_to is not None:
            replies.append(comment)
        else:
            cid = comment.get("id")
            threads_by_id[cid] = {
                "id": str(cid),
                "type": "inline" if comment.get("path") else "general",
                "file_path": comment.get("path"),
                "line_number": comment.get("line") or comment.get("original_line"),
                "body": comment.get("body", ""),
                "author": comment.get("user", {}).get("login", ""),
                "created_at": comment.get("created_at", ""),
                "replies": [],
            }

    # Attach replies to their parent threads
    for reply in replies:
        parent_id = reply.get("in_reply_to_id")
        parent = threads_by_id.get(parent_id)
        reply_data = {
            "author": reply.get("user", {}).get("login", ""),
            "body": reply.get("body", ""),
            "created_at": reply.get("created_at", ""),
        }
        if parent:
            parent["replies"].append(reply_data)
        else:
            # Orphan reply (parent deleted etc.) — create a standalone thread
            rid = reply.get("id")
            threads_by_id[rid] = {
                "id": str(rid),
                "type": "general",
                "file_path": reply.get("path"),
                "line_number": reply.get("line") or reply.get("original_line"),
                "body": reply.get("body", ""),
                "author": reply.get("user", {}).get("login", ""),
                "created_at": reply.get("created_at", ""),
                "replies": [],
            }

    discussions = list(threads_by_id.values())

    return {
        "success": True,
        "pr_id": pr_id,
        "discussions": discussions,
        "total": len(discussions),
    }


async def _reply_to_pr_comment(
    pr_id: str, comment_id: str, body: str, repo_root: str
) -> dict:
    """Reply to a specific review comment on a GitHub PR."""
    try:
        pr_id = validate_pr_id(pr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    if not body or not body.strip():
        return {"success": False, "error": "Reply body is empty.", "error_type": "validation_error"}

    # Get owner/repo
    repo_id = await _get_github_repo_id(repo_root)
    if not repo_id:
        return {
            "success": False,
            "error": "Could not determine GitHub repo (owner/repo). Run 'gh repo set-default'.",
            "error_type": "repo_not_found",
        }

    r = await run_exec(
        [
            "gh", "api",
            f"repos/{repo_id}/pulls/{pr_id}/comments",
            "--method", "POST",
            "--field", f"body={body}",
            "--field", f"in_reply_to={comment_id}",
        ],
        cwd=repo_root,
    )
    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to reply: {r.stderr}",
            "error_type": "post_failed",
        }

    return {
        "success": True,
        "pr_id": pr_id,
        "comment_id": comment_id,
        "action": "reply_posted",
    }


# ── FastMCP Server ────────────────────────────────────────

from mcp.server.fastmcp import FastMCP

mcp_server = FastMCP("omniforge")


@mcp_server.tool()
async def fetch_mr_data(mr_id: str, repo_root: str) -> str:
    """Fetch all GitLab MR data (metadata, comments, diff, commits) in one call.

    Args:
        mr_id: Merge request number (e.g., '136' or '!136')
        repo_root: Absolute path to the git repository root
    """
    result = await _fetch_mr_data(mr_id, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def create_review_worktrees(
    mr_id: str, source_branch: str, repo_root: str
) -> str:
    """Create 3 isolated git worktrees for OmniForge agents.

    Args:
        mr_id: Merge request number
        source_branch: MR source branch (from fetch_mr_data response)
        repo_root: Absolute path to the git repository root
    """
    result = await _create_review_worktrees(mr_id, source_branch, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def cleanup_review_worktrees(mr_id: str, repo_root: str) -> str:
    """Remove all OmniForge worktrees for a given MR.

    Args:
        mr_id: Merge request number
        repo_root: Absolute path to the git repository root
    """
    result = await _cleanup_review_worktrees(mr_id, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def post_review_summary(mr_id: str, summary: str, repo_root: str) -> str:
    """Post a top-level summary comment on a GitLab MR.

    Args:
        mr_id: Merge request number
        summary: The formatted summary text to post as an MR comment
        repo_root: Absolute path to the git repository root
    """
    result = await _post_review_summary(mr_id, summary, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def post_inline_thread(
    mr_id: str,
    file_path: str,
    line_number: int,
    body: str,
    repo_root: str,
) -> str:
    """Post an inline discussion thread on a specific line of an MR diff.

    The tool automatically fetches the required SHA values and constructs
    the GitLab API position data. Just provide the file, line, and text.

    Args:
        mr_id: Merge request number
        file_path: Path to the file in the diff (e.g., 'src/app.py')
        line_number: Line number in the new version of the file
        body: The formatted finding text to post as a discussion thread
        repo_root: Absolute path to the git repository root
    """
    result = await _post_inline_thread(mr_id, file_path, line_number, body, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def post_full_review(
    mr_id: str,
    summary: str,
    findings: str,
    repo_root: str,
) -> str:
    """Post a complete review: summary comment + inline threads for each finding.

    This is the recommended way to post review results. Posts the summary as
    a top-level MR comment, then creates individual inline discussion threads
    for each finding on the exact diff line.

    Args:
        mr_id: Merge request number
        summary: The formatted summary text for the top-level comment
        findings: JSON string of an array of findings, each with: file_path (str), line_number (int), body (str)
        repo_root: Absolute path to the git repository root
    """
    try:
        findings_list = json.loads(findings)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({
            "success": False,
            "error": "findings must be a valid JSON array string",
            "error_type": "validation_error",
        }, indent=2)
    result = await _post_full_review(mr_id, summary, findings_list, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def map_diff_lines(diff_text: str) -> str:
    """Parse a unified diff and return exact changed line numbers per file.

    Use this to find valid line numbers for posting inline discussion threads.
    Returns a map of file paths to their added lines, all visible lines, and hunk ranges.
    The line numbers are for the NEW version of the file (what GitLab expects).

    Args:
        diff_text: Raw unified diff text (from fetch_mr_data response's "diff" field)
    """
    result = parse_diff_line_map(diff_text)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def create_linked_issue(
    mr_id: str,
    title: str,
    description: str,
    labels: str,
    repo_root: str,
) -> str:
    """Create a GitLab issue linked to a merge request.

    The issue is automatically linked to the MR via GitLab's --linked-mr flag,
    so anyone viewing the issue can navigate to the original MR.

    Args:
        mr_id: Merge request number the issue relates to
        title: Issue title (e.g., '[MR !136] Missing null check in auth handler')
        description: Issue body with finding details, impact, and recommendation
        labels: Comma-separated labels (e.g., 'omniforge,bug') or empty string for none
        repo_root: Absolute path to the git repository root
    """
    result = await _create_linked_issue(mr_id, title, description, labels, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def fetch_mr_discussions(mr_id: str, repo_root: str) -> str:
    """Fetch all discussion threads from a GitLab MR with structured data.

    Returns discussions with type (inline/general), file positions, bodies,
    replies, and resolved status. Uses --paginate for complete results.

    Args:
        mr_id: Merge request number
        repo_root: Absolute path to the git repository root
    """
    result = await _fetch_mr_discussions(mr_id, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def reply_to_discussion(
    mr_id: str, discussion_id: str, body: str, repo_root: str
) -> str:
    """Post a reply to a specific discussion thread on a GitLab MR.

    Args:
        mr_id: Merge request number
        discussion_id: The discussion thread ID to reply to
        body: Reply text (markdown supported)
        repo_root: Absolute path to the git repository root
    """
    result = await _reply_to_discussion(mr_id, discussion_id, body, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def resolve_discussion(
    mr_id: str, discussion_id: str, resolved: bool, repo_root: str
) -> str:
    """Resolve or unresolve a discussion thread on a GitLab MR.

    Args:
        mr_id: Merge request number
        discussion_id: The discussion thread ID
        resolved: True to resolve, False to unresolve
        repo_root: Absolute path to the git repository root
    """
    result = await _resolve_discussion(mr_id, discussion_id, resolved, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def update_mr_labels(mr_id: str, repo_root: str,
                           add_labels: str = "",
                           remove_labels: str = "") -> str:
    """Add and/or remove labels on a GitLab merge request in a single call.

    Uses add_labels/remove_labels semantics (labels auto-create on add; no
    GET-merge-PUT race). Pass comma-separated label names.

    Args:
        mr_id: Merge request number
        repo_root: Absolute path to the git repository root
        add_labels: Comma-separated labels to add (may be new labels)
        remove_labels: Comma-separated labels to remove
    """
    result = await _update_mr_labels(mr_id, add_labels, remove_labels, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def post_mr_note(mr_id: str, body: str, repo_root: str) -> str:
    """Post a top-level note on a GitLab MR and return its discussion id.

    The discussion id is the durable thread identity to record when asking
    an author for something (e.g. a rendered-UI screenshot); replies are
    matched on it.

    Args:
        mr_id: Merge request number
        body: Note text (markdown supported)
        repo_root: Absolute path to the git repository root
    """
    result = await _post_mr_note(mr_id, body, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def upload_project_file(file_path: str, repo_root: str) -> str:
    """Upload a file to the project (GitLab uploads API) and return markdown.

    The returned markdown embeds the upload verbatim and can be included in
    notes. Sized to GitLab's 100 MiB uploads limit.

    Args:
        file_path: Absolute path to the file to upload
        repo_root: Absolute path to the git repository root
    """
    result = await _upload_project_file(file_path, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def cleanup_omnifix_worktrees(mr_id: str, repo_root: str) -> str:
    """Remove all OmniFix worktrees and temp branches for a given MR.

    Cleans up: fix worktree (.worktrees/omnifix-{mr_id}), triage worktrees
    (.worktrees/omnifix-triage-{mr_id}-*), and the temp branch (omnifix-temp-{mr_id}).

    Args:
        mr_id: Merge request number
        repo_root: Absolute path to the git repository root
    """
    result = await _cleanup_omnifix_worktrees(mr_id, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def approve_mr(mr_id: str, repo_root: str, sha: str = "",
                     checked_sha: str = "") -> str:
    """Approve a GitLab merge request.

    Approves as the OmniCheck bot when the OMNICHECK_BOT_TOKEN environment
    variable is set, otherwise as the current glab user. This lets a separate
    bot account approve MRs even when "Prevent approval by creator/committer"
    would block the human user. The approval is always pinned to a commit sha
    (the provided sha, or the MR HEAD sha resolved automatically) so it goes
    stale if new commits land; if no sha can be resolved the call is refused.

    Guards (fail-closed): refuses when unresolved resolvable threads remain,
    when the MR lacks the omniforge::reviewed label, or when checked_sha (the
    head the verification ran against) differs from the live head.

    Args:
        mr_id: Merge request number (e.g., '136' or '!136')
        repo_root: Absolute path to the git repository root
        sha: Optional git commit SHA to pin the approval to (defaults to MR HEAD)
        checked_sha: Optional head sha the verification ran against; a
            mismatch with the live head refuses the approval
    """
    result = await _approve_mr(mr_id, repo_root, sha, checked_sha)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def create_gitlab_mr(
    repo_root: str,
    title: str = "",
    description: str = "",
    target_branch: str = "main",
    source_branch: str = "",
    assignees: str = "",
    reviewers: str = "",
    labels: str = "",
    draft: bool = False,
    fill: bool = True,
    fill_commit_body: bool = True,
    push: bool = True,
    related_issue: str = "",
    copy_issue_labels: bool = False,
    remove_source_branch: bool = False,
    squash_before_merge: bool = False,
    milestone: str = "",
    web: bool = False,
) -> str:
    """Create a GitLab merge request using glab CLI with safe execution.

    This tool wraps glab mr create with proper validation and safe subprocess execution.
    It auto-populates title and description from commit messages when fill=True.

    Args:
        repo_root: Absolute path to the git repository root
        title: Custom MR title (overrides --fill if provided)
        description: Custom MR description (overrides --fill if provided)
        target_branch: Target branch for the MR (default: main)
        source_branch: Source branch for the MR (default: current branch)
        assignees: Comma-separated usernames to assign
        reviewers: Comma-separated usernames to request review from
        labels: Comma-separated label names
        draft: Whether to create as draft MR
        fill: Auto-populate title and description from commits
        fill_commit_body: Include commit bodies in description
        push: Push the branch if not already pushed
        related_issue: Issue number to link MR to
        copy_issue_labels: Copy labels from related issue
        remove_source_branch: Delete source branch after merge
        squash_before_merge: Squash commits when merging
        milestone: Milestone ID or title
        web: Open in browser for editing
    """
    result = await _create_gitlab_mr(
        repo_root=repo_root,
        title=title if title else None,
        description=description if description else None,
        target_branch=target_branch,
        source_branch=source_branch if source_branch else None,
        assignees=assignees if assignees else None,
        reviewers=reviewers if reviewers else None,
        labels=labels if labels else None,
        draft=draft,
        fill=fill,
        fill_commit_body=fill_commit_body,
        push=push,
        related_issue=related_issue if related_issue else None,
        copy_issue_labels=copy_issue_labels,
        remove_source_branch=remove_source_branch,
        squash_before_merge=squash_before_merge,
        milestone=milestone if milestone else None,
        web=web,
    )
    return json.dumps(result, indent=2)


# ── GitHub FastMCP Tools ───────────────────────────────────


@mcp_server.tool()
async def fetch_pr_data(pr_id: str, repo_root: str) -> str:
    """Fetch all GitHub PR data (metadata, comments, diff, commits) in one call.

    Args:
        pr_id: Pull request number (e.g., '42' or '#42')
        repo_root: Absolute path to the git repository root
    """
    result = await _fetch_pr_data(pr_id, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def create_github_pr(
    repo_root: str,
    title: str = "",
    description: str = "",
    target_branch: str = "main",
    source_branch: str = "",
    assignees: str = "",
    reviewers: str = "",
    labels: str = "",
    draft: bool = False,
    web: bool = False,
) -> str:
    """Create a GitHub pull request using gh CLI with safe execution.

    Args:
        repo_root: Absolute path to the git repository root
        title: Custom PR title
        description: Custom PR description body
        target_branch: Target/base branch for the PR (default: main)
        source_branch: Source/head branch for the PR (default: current branch)
        assignees: Comma-separated usernames to assign
        reviewers: Comma-separated usernames to request review from
        labels: Comma-separated label names
        draft: Whether to create as draft PR
        web: Open in browser for editing
    """
    result = await _create_github_pr(
        repo_root=repo_root,
        title=title if title else None,
        description=description if description else None,
        target_branch=target_branch,
        source_branch=source_branch if source_branch else None,
        assignees=assignees if assignees else None,
        reviewers=reviewers if reviewers else None,
        labels=labels if labels else None,
        draft=draft,
        web=web,
    )
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def post_pr_review_summary(pr_id: str, summary: str, repo_root: str) -> str:
    """Post a top-level summary comment on a GitHub PR.

    Args:
        pr_id: Pull request number
        summary: The formatted summary text to post as a PR comment
        repo_root: Absolute path to the git repository root
    """
    result = await _post_pr_review_summary(pr_id, summary, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def post_pr_inline_thread(
    pr_id: str,
    file_path: str,
    line_number: int,
    body: str,
    repo_root: str,
) -> str:
    """Post an inline review comment on a specific line of a GitHub PR diff.

    The tool automatically fetches the required HEAD SHA and constructs
    the GitHub API position data. Just provide the file, line, and text.

    Args:
        pr_id: Pull request number
        file_path: Path to the file in the diff (e.g., 'src/app.py')
        line_number: Line number in the new version of the file
        body: The formatted finding text to post as a review comment
        repo_root: Absolute path to the git repository root
    """
    result = await _post_pr_inline_thread(pr_id, file_path, line_number, body, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def fetch_pr_discussions(pr_id: str, repo_root: str) -> str:
    """Fetch all review comments from a GitHub PR with structured data.

    Returns comments grouped into threads with file positions, bodies,
    replies, and author info. Uses --paginate for complete results.

    Args:
        pr_id: Pull request number
        repo_root: Absolute path to the git repository root
    """
    result = await _fetch_pr_discussions(pr_id, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def reply_to_pr_comment(
    pr_id: str, comment_id: str, body: str, repo_root: str
) -> str:
    """Reply to a specific review comment on a GitHub PR.

    Args:
        pr_id: Pull request number
        comment_id: The review comment ID to reply to
        body: Reply text (markdown supported)
        repo_root: Absolute path to the git repository root
    """
    result = await _reply_to_pr_comment(pr_id, comment_id, body, repo_root)
    return json.dumps(result, indent=2)


# ── GitLab MR Creation ─────────────────────────────────


def validate_target_branch(branch: str) -> str:
    """Validate target branch name contains no shell metacharacters."""
    return validate_branch_name(branch)


def validate_title(title: str) -> str:
    """Validate MR title is not empty and contains no shell metacharacters."""
    if not title or not title.strip():
        raise ValueError("MR title cannot be empty.")
    # Allow basic punctuation but block shell metacharacters
    if re.search(r'[;&|$`\\]', title):
        raise ValueError(f"Invalid characters in title: {title}")
    return title.strip()


def validate_labels(labels: str) -> str:
    """Validate labels format (comma-separated) and reject control characters."""
    if not labels:
        return ""
    # Allow GitLab-style labels (including punctuation like "type::bug", "group/backend").
    # Since arguments are passed via create_subprocess_exec (no shell), we only block
    # control characters (including newlines) which can break CLI parsing.
    if re.search(r'[\x00-\x1F\x7F]', labels):
        raise ValueError(f"Invalid control characters in labels: {labels!r}")
    return labels.strip()


async def _create_gitlab_mr(
    repo_root: str,
    title: str = None,
    description: str = None,
    target_branch: str = "main",
    source_branch: str = None,
    assignees: str = None,
    reviewers: str = None,
    labels: str = None,
    draft: bool = False,
    fill: bool = True,
    fill_commit_body: bool = True,
    push: bool = True,
    related_issue: str = None,
    copy_issue_labels: bool = False,
    remove_source_branch: bool = False,
    squash_before_merge: bool = False,
    milestone: str = None,
    web: bool = False,
) -> dict:
    """Create a GitLab merge request using glab CLI safely."""
    try:
        repo_root = validate_repo_root(repo_root)
        if target_branch:
            target_branch = validate_target_branch(target_branch)
        if source_branch:
            source_branch = validate_branch_name(source_branch)
        if title:
            title = validate_title(title)
        if labels:
            labels = validate_labels(labels)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    # Build the command args safely (list of strings, no shell interpretation)
    # --web already skips all prompts that --yes skips, so they conflict
    args = ["glab", "mr", "create"]
    if not web:
        args.append("--yes")

    # Add flags
    if fill:
        args.append("--fill")
    if fill and fill_commit_body:
        args.append("--fill-commit-body")
    if push:
        args.append("--push")
    if draft:
        args.append("--draft")
    if web:
        args.append("--web")
    if remove_source_branch:
        args.append("--remove-source-branch")
    if squash_before_merge:
        args.append("--squash-before-merge")
    if copy_issue_labels and related_issue:
        args.append("--copy-issue-labels")

    # Add options with values
    if target_branch:
        args.extend(["--target-branch", target_branch])
    if source_branch:
        args.extend(["--source-branch", source_branch])
    if title:
        args.extend(["--title", title])
    if description:
        args.extend(["--description", description])
    if assignees:
        args.extend(["--assignee", assignees])
    if reviewers:
        args.extend(["--reviewer", reviewers])
    if labels:
        args.extend(["--label", labels])
    if related_issue:
        args.extend(["--related-issue", related_issue])
    if milestone:
        args.extend(["--milestone", milestone])

    r = await run_exec(args, cwd=repo_root, timeout=120)

    if r.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to create MR: {r.stderr}",
            "error_type": "mr_creation_failed",
        }

    # Parse MR URL from output
    mr_url = ""
    for line in r.stdout.strip().split("\n"):
        for word in line.split():
            if word.startswith("http"):
                mr_url = word
                break
        if mr_url:
            break

    return {
        "success": True,
        "mr_url": mr_url,
        "output": r.stdout.strip(),
        "action": "mr_created",
    }


# ── Entry Point ───────────────────────────────────────────

if __name__ == "__main__":
    mcp_server.run()
