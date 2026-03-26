#!/usr/bin/env python3
"""OmniReview MCP Server — worktree and MR data tools for code review."""

import asyncio
import json
import os
import re
import shutil

# ── Constants ──────────────────────────────────────────────

WORKTREE_TYPES = ["analyst", "codebase", "security"]
MAX_DIFF_LINES = 10000

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
    if not os.path.isdir(os.path.join(repo_root, ".git")):
        raise ValueError(f"Not a git repository: {repo_root}")
    return repo_root


def validate_branch_name(branch: str) -> str:
    """Validate branch name contains no shell metacharacters."""
    if re.search(r'[;&|$`\\\'\"(){}\[\]!#~]', branch):
        raise ValueError(f"Invalid branch name: {branch}")
    return branch


# ── Safe Command Runner ───────────────────────────────────


async def run_subprocess(args: list, cwd: str, timeout: int = 60):
    """Run a command safely via subprocess_exec (no shell interpretation).

    Uses asyncio.create_subprocess_exec which passes args directly
    to the OS without shell interpretation, preventing injection.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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
    """Truncate diff if it exceeds MAX_DIFF_LINES."""
    if line_count <= MAX_DIFF_LINES:
        return diff_text, False
    lines = diff_text.split('\n')[:MAX_DIFF_LINES]
    truncated = '\n'.join(lines)
    truncated += (
        f"\n\n... [TRUNCATED: {line_count} total lines, "
        f"showing first {MAX_DIFF_LINES}] ..."
    )
    return truncated, True


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
        "diff_line_map": diff_line_map,
        "commits": parse_commits(commits_r.stdout),
        "files_changed": files_changed,
        "labels": metadata.get("labels", []),
        "assignees": [a.get("username", "") for a in metadata.get("assignees", [])],
        "reviewers": [r.get("username", "") for r in metadata.get("reviewers", [])],
    }


async def _create_review_worktrees(mr_id: str, source_branch: str, repo_root: str) -> dict:
    """Create 3 isolated git worktrees for OmniReview agents."""
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
        source_branch = validate_branch_name(source_branch)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    worktrees_dir = os.path.join(repo_root, ".worktrees")
    os.makedirs(worktrees_dir, exist_ok=True)

    # Ensure .worktrees/ is in .gitignore
    gitignore_path = os.path.join(repo_root, ".gitignore")
    result = await run_exec(["git", "check-ignore", "-q", ".worktrees"], cwd=repo_root)
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
                cwd=repo_root, timeout=30,
            )
            stale_count += 1
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)

    await run_exec(["git", "worktree", "prune"], cwd=repo_root)

    # Fetch source branch
    fetch = await run_exec(
        ["git", "fetch", "origin", source_branch],
        cwd=repo_root, timeout=120,
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
            cwd=repo_root, timeout=30,
        )
        if r.returncode != 0:
            for _, cp in created.items():
                await run_exec(
                    ["git", "worktree", "remove", cp, "--force"],
                    cwd=repo_root, timeout=30,
                )
            await run_exec(["git", "worktree", "prune"], cwd=repo_root)
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
    """Remove all OmniReview worktrees for a given MR."""
    try:
        mr_id = validate_mr_id(mr_id)
        repo_root = validate_repo_root(repo_root)
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "validation_error"}

    worktrees_dir = os.path.join(repo_root, ".worktrees")
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
            cwd=repo_root, timeout=30,
        )

        if os.path.exists(path):
            try:
                shutil.rmtree(path)
            except Exception as e:
                errors.append(f"Failed to remove {name}: {e}")
                continue

        removed.append(name)

    await run_exec(["git", "worktree", "prune"], cwd=repo_root)

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


async def _post_inline_thread(
    mr_id: str,
    file_path: str,
    line_number: int,
    body: str,
    repo_root: str,
) -> dict:
    """Post an inline discussion thread on a specific diff line."""
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

    # Post summary
    summary_result = await _post_review_summary(mr_id, summary, repo_root)
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

        thread_result = await _post_inline_thread(mr_id, fp, ln, body, repo_root)
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


# ── FastMCP Server ────────────────────────────────────────

from mcp.server.fastmcp import FastMCP

mcp_server = FastMCP("omnireview")


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
    """Create 3 isolated git worktrees for OmniReview agents.

    Args:
        mr_id: Merge request number
        source_branch: MR source branch (from fetch_mr_data response)
        repo_root: Absolute path to the git repository root
    """
    result = await _create_review_worktrees(mr_id, source_branch, repo_root)
    return json.dumps(result, indent=2)


@mcp_server.tool()
async def cleanup_review_worktrees(mr_id: str, repo_root: str) -> str:
    """Remove all OmniReview worktrees for a given MR.

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
        labels: Comma-separated labels (e.g., 'omnireview,bug') or empty string for none
        repo_root: Absolute path to the git repository root
    """
    result = await _create_linked_issue(mr_id, title, description, labels, repo_root)
    return json.dumps(result, indent=2)


# ── Entry Point ───────────────────────────────────────────

if __name__ == "__main__":
    mcp_server.run()
