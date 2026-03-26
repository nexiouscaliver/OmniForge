# OmniReview MCP Tools v0.1 — Design Spec

## Context

OmniReview currently performs all operations via raw Bash commands — `glab` CLI calls, `git worktree` management, file operations. This works but has problems:

- **25+ bash commands** per review session (fragile, verbose, permission-prompt heavy)
- **No structured error handling** — a failed `git fetch` doesn't cleanly abort the review
- **Stale worktree accumulation** — cleanup depends on the skill reaching Phase 7
- **Path handling bugs** — relative vs absolute path conversion is manual and error-prone

This spec introduces a Python MCP server that exposes 3 dedicated tools, replacing the most error-prone and repetitive operations with structured, typed, error-handled tool calls.

## Goals

1. Replace ~25 bash commands with 3 tool calls (Phases 1, 2, 7)
2. Add proper error handling with structured error responses
3. Eliminate worktree path conversion bugs
4. Make cleanup more reliable (single call, handles partial state)
5. Build the foundation for future tool expansion (posting, issue creation, etc.)

## Non-Goals

- Not replacing the agent dispatch mechanism (Phase 3)
- Not replacing the consolidation logic (Phase 4)
- Not building a GitLab API wrapper (using `glab` CLI under the hood)
- Not adding GitHub/other platform support yet

---

## Architecture

### MCP Server

A Python MCP server using the `mcp` package (Model Context Protocol SDK). Runs as a local stdio-based process spawned by Claude Code at session start.

```
Claude Code
    │
    ├── reads .mcp.json
    ├── spawns: python3 tools/omnireview_mcp_server.py
    │
    └── calls tools via JSON-RPC over stdin/stdout:
        ├── mcp__omnireview__fetch_mr_data
        ├── mcp__omnireview__create_review_worktrees
        └── mcp__omnireview__cleanup_review_worktrees
```

### Registration

**.mcp.json** (at plugin root):
```json
{
  "omnireview": {
    "command": "python3",
    "args": ["${CLAUDE_PLUGIN_ROOT}/tools/omnireview_mcp_server.py"]
  }
}
```

### File Structure

```
OmniReview/
├── tools/
│   ├── omnireview_mcp_server.py    # MCP server implementation
│   └── requirements.txt            # Dependencies
├── .mcp.json                       # Server registration
└── ... (existing files unchanged)
```

### Dependencies

```
# tools/requirements.txt
mcp>=1.0.0,<2.0.0
```

The `mcp` Python package provides the MCP protocol implementation (JSON-RPC over stdio, tool registration, schema validation). We use the `FastMCP` high-level API for simpler tool registration.

---

## Security: Input Validation and Command Execution

**All subprocess calls MUST use `asyncio.create_subprocess_exec` (argument list), never `create_subprocess_shell` (string interpolation).** This prevents shell injection via crafted branch names, MR IDs, or paths.

**Input validation at tool entry:**
- `mr_id` must match `^\d+$` after stripping leading `!`
- `repo_root` must be an absolute path, must exist, must contain `.git/` directory
- `source_branch` must not contain shell metacharacters

```python
import re

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
```

**Subprocess execution (safe, with timeout):**

```python
async def run_exec(args: list[str], cwd: str, timeout: int = 60):
    """Run a command safely via exec (no shell). Returns CompletedProcess."""
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return type('Result', (), {
            'returncode': -1, 'stdout': '', 'stderr': 'Command timed out'
        })()
    return type('Result', (), {
        'returncode': proc.returncode,
        'stdout': stdout.decode(),
        'stderr': stderr.decode()
    })()
```

**Timeout defaults:**
- Metadata/auth calls: 60 seconds
- `git fetch` and `glab mr diff`: 120 seconds
- Worktree create/remove: 30 seconds

---

## Helper Functions

```python
def extract_changed_files(diff_text: str) -> list[str]:
    """Extract file paths from unified diff output."""
    files = []
    for line in diff_text.split('\n'):
        if line.startswith('+++ b/'):
            files.append(line[6:])
        elif line.startswith('diff --git a/'):
            parts = line.split(' b/')
            if len(parts) >= 2:
                path = parts[-1]
                if path not in files:
                    files.append(path)
    return files

def parse_commits(log_output: str) -> list[dict]:
    """Parse git log --oneline output into structured commits."""
    commits = []
    for line in log_output.strip().split('\n'):
        if line.strip():
            parts = line.split(' ', 1)
            commits.append({
                "sha": parts[0],
                "message": parts[1] if len(parts) > 1 else ""
            })
    return commits

MAX_DIFF_LINES = 10000

def truncate_diff_if_needed(diff_text: str, line_count: int) -> tuple:
    """Truncate diff if it exceeds MAX_DIFF_LINES. Returns (diff, was_truncated)."""
    if line_count <= MAX_DIFF_LINES:
        return diff_text, False
    lines = diff_text.split('\n')[:MAX_DIFF_LINES]
    truncated = '\n'.join(lines)
    truncated += f"\n\n... [TRUNCATED: {line_count} total lines, showing first {MAX_DIFF_LINES}] ..."
    return truncated, True
```

---

## Tool Specifications

### Tool 1: `fetch_mr_data`

**Purpose:** Fetch all MR data in a single call — metadata, comments, diff, commits — and return a structured package ready for agent injection.

**Replaces:** Phase 1 (6 bash commands: `glab auth status`, `glab mr view -F json`, `glab mr view -c`, `glab mr diff --raw`, `git fetch`, `git log`)

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "mr_id": {
      "type": "string",
      "description": "Merge request number (e.g., '136' or '!136')"
    },
    "repo_root": {
      "type": "string",
      "description": "Absolute path to the git repository root"
    }
  },
  "required": ["mr_id", "repo_root"]
}
```

#### Success Response

```json
{
  "success": true,
  "mr_id": "136",
  "title": "feat: add Stripe configuration for staging",
  "author": "Abhishek-2004-17",
  "source_branch": "stripe-secret-exchange",
  "target_branch": "staging-phase",
  "pipeline_status": "success",
  "description": "Full MR description text...",
  "comments": "All discussion threads...",
  "diff": "Raw diff output...",
  "diff_line_count": 86,
  "diff_too_large": false,
  "commits": [
    {"sha": "0e3cc353", "message": "feat: add Stripe configuration for staging"}
  ],
  "files_changed": [".gitlab-ci.yml"],
  "labels": [],
  "assignees": [],
  "reviewers": []
}
```

#### Error Responses

```json
{
  "success": false,
  "error": "glab not authenticated. Run 'glab auth login' first.",
  "error_type": "auth_failure"
}
```

```json
{
  "success": false,
  "error": "MR !999 not found in this repository.",
  "error_type": "mr_not_found"
}
```

```json
{
  "success": false,
  "error": "Network error fetching MR data. Check your connection.",
  "error_type": "network_error"
}
```

#### Internal Implementation

```python
async def fetch_mr_data(mr_id: str, repo_root: str) -> dict:
    # 1. Validate inputs
    mr_id = validate_mr_id(mr_id)
    repo_root = validate_repo_root(repo_root)

    # 2. Verify glab auth
    auth_result = await run_exec(["glab", "auth", "status"], cwd=repo_root)
    if auth_result.returncode != 0:
        return {"success": False, "error": "glab not authenticated. Run 'glab auth login'.", "error_type": "auth_failure"}

    # 3. Fetch MR metadata (JSON)
    json_result = await run_exec(["glab", "mr", "view", mr_id, "-F", "json"], cwd=repo_root)
    if json_result.returncode != 0:
        return {"success": False, "error": f"MR !{mr_id} not found.", "error_type": "mr_not_found"}
    metadata = json.loads(json_result.stdout)

    # 4. Fetch comments (default to empty on failure)
    comments_result = await run_exec(["glab", "mr", "view", mr_id, "-c"], cwd=repo_root)
    comments = comments_result.stdout if comments_result.returncode == 0 else ""

    # 5. Fetch diff (with longer timeout for large MRs)
    diff_result = await run_exec(["glab", "mr", "diff", mr_id, "--raw"], cwd=repo_root, timeout=120)
    diff_lines = diff_result.stdout.count('\n')

    # 6. Extract branches from metadata
    source_branch = metadata.get("source_branch", "")
    target_branch = metadata.get("target_branch", "")

    # 7. Fetch BOTH branches (target may be stale) and get commit list
    await run_exec(["git", "fetch", "origin", source_branch, target_branch], cwd=repo_root, timeout=120)
    commits_result = await run_exec(
        ["git", "log", "--oneline", f"origin/{target_branch}..origin/{source_branch}"],
        cwd=repo_root
    )

    # 8. Parse changed files from diff
    files_changed = extract_changed_files(diff_result.stdout)

    # 9. Truncate diff if too large (prevents context window overflow)
    diff_text, diff_truncated = truncate_diff_if_needed(diff_result.stdout, diff_lines)

    # 10. Return structured package
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
        "commits": parse_commits(commits_result.stdout),
        "files_changed": files_changed,
        "labels": metadata.get("labels", []),
        "assignees": [a.get("username", "") for a in metadata.get("assignees", [])],
        "reviewers": [r.get("username", "") for r in metadata.get("reviewers", [])]
    }
```

---

### Tool 2: `create_review_worktrees`

**Purpose:** Create all 3 isolated worktrees for an OmniReview session in a single call, with automatic stale cleanup and absolute path resolution.

**Replaces:** Phase 2 (12 bash commands: gitignore check, stale cleanup ×3, prune, fetch, worktree add ×3, path resolve ×3)

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "mr_id": {
      "type": "string",
      "description": "Merge request number"
    },
    "source_branch": {
      "type": "string",
      "description": "MR source branch name (from fetch_mr_data response)"
    },
    "repo_root": {
      "type": "string",
      "description": "Absolute path to the git repository root"
    }
  },
  "required": ["mr_id", "source_branch", "repo_root"]
}
```

#### Success Response

```json
{
  "success": true,
  "mr_id": "136",
  "worktrees": {
    "analyst": "/Users/shahil/work/regenai-repo/cleo/.worktrees/omni-analyst-136",
    "codebase": "/Users/shahil/work/regenai-repo/cleo/.worktrees/omni-codebase-136",
    "security": "/Users/shahil/work/regenai-repo/cleo/.worktrees/omni-security-136"
  },
  "source_branch": "stripe-secret-exchange",
  "stale_cleaned": 0
}
```

#### Error Response (with partial cleanup)

```json
{
  "success": false,
  "error": "Failed to create worktree 'omni-security-136': branch conflict",
  "error_type": "worktree_creation_failed",
  "partial_worktrees": {
    "analyst": "/abs/path/.worktrees/omni-analyst-136",
    "codebase": "/abs/path/.worktrees/omni-codebase-136"
  },
  "cleanup_performed": true
}
```

#### Internal Implementation

```python
WORKTREE_TYPES = ["analyst", "codebase", "security"]

async def create_review_worktrees(mr_id: str, source_branch: str, repo_root: str) -> dict:
    # Validate all inputs
    mr_id = validate_mr_id(mr_id)
    repo_root = validate_repo_root(repo_root)
    source_branch = validate_branch_name(source_branch)
    worktrees_dir = os.path.join(repo_root, ".worktrees")

    # 1. Ensure .worktrees/ exists
    os.makedirs(worktrees_dir, exist_ok=True)

    # 2. Verify .worktrees/ is in .gitignore (safely append with newline check)
    gitignore_path = os.path.join(repo_root, ".gitignore")
    result = await run_exec(["git", "check-ignore", "-q", ".worktrees"], cwd=repo_root)
    if result.returncode != 0:
        # Read existing content to check if it ends with newline
        existing = ""
        if os.path.exists(gitignore_path):
            with open(gitignore_path, "r") as f:
                existing = f.read()
        prefix = "" if existing.endswith("\n") or not existing else "\n"
        with open(gitignore_path, "a") as f:
            f.write(f"{prefix}.worktrees/\n")

    # 3. Clean stale worktrees from previous run
    stale_count = 0
    for wt_type in WORKTREE_TYPES:
        name = f"omni-{wt_type}-{mr_id}"
        path = os.path.join(worktrees_dir, name)
        if os.path.exists(path):
            await run_exec(["git", "worktree", "remove", path, "--force"], cwd=repo_root, timeout=30)
            stale_count += 1
            # Force-remove directory if worktree remove didn't clean it
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)

    await run_exec(["git", "worktree", "prune"], cwd=repo_root)

    # 4. Fetch source branch (with longer timeout)
    fetch_result = await run_exec(
        ["git", "fetch", "origin", source_branch], cwd=repo_root, timeout=120
    )
    if fetch_result.returncode != 0:
        return {
            "success": False,
            "error": f"Failed to fetch branch: origin/{source_branch}",
            "error_type": "fetch_failed"
        }

    # 5. Create 3 worktrees
    created = {}
    for wt_type in WORKTREE_TYPES:
        name = f"omni-{wt_type}-{mr_id}"
        path = os.path.join(worktrees_dir, name)
        result = await run_exec(
            ["git", "worktree", "add", path, f"origin/{source_branch}", "--detach"],
            cwd=repo_root, timeout=30
        )
        if result.returncode != 0:
            # Cleanup already-created worktrees
            for created_type, created_path in created.items():
                await run_exec(["git", "worktree", "remove", created_path, "--force"], cwd=repo_root, timeout=30)
            await run_exec(["git", "worktree", "prune"], cwd=repo_root)
            return {
                "success": False,
                "error": f"Failed to create worktree '{name}': {result.stderr}",
                "error_type": "worktree_creation_failed",
                "partial_worktrees": created,
                "cleanup_performed": True
            }
        # Resolve absolute path
        created[wt_type] = os.path.abspath(path)

    return {
        "success": True,
        "mr_id": mr_id,
        "worktrees": created,
        "source_branch": source_branch,
        "stale_cleaned": stale_count
    }
```

---

### Tool 3: `cleanup_review_worktrees`

**Purpose:** Reliably remove all OmniReview worktrees for a given MR, handling partial state and ensuring nothing is left behind.

**Replaces:** Phase 7 (7 bash commands: worktree remove ×3, rm -rf ×3, prune)

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "mr_id": {
      "type": "string",
      "description": "Merge request number"
    },
    "repo_root": {
      "type": "string",
      "description": "Absolute path to the git repository root"
    }
  },
  "required": ["mr_id", "repo_root"]
}
```

#### Success Response

```json
{
  "success": true,
  "removed": ["omni-analyst-136", "omni-codebase-136", "omni-security-136"],
  "already_clean": [],
  "errors": []
}
```

#### Partial Cleanup Response

```json
{
  "success": true,
  "removed": ["omni-analyst-136", "omni-codebase-136"],
  "already_clean": ["omni-security-136"],
  "errors": []
}
```

#### Internal Implementation

```python
async def cleanup_review_worktrees(mr_id: str, repo_root: str) -> dict:
    # Validate inputs
    mr_id = validate_mr_id(mr_id)
    repo_root = validate_repo_root(repo_root)
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

        # Try git worktree remove first (safe exec, no shell)
        await run_exec(["git", "worktree", "remove", path, "--force"], cwd=repo_root, timeout=30)

        # Force-remove directory if still exists
        if os.path.exists(path):
            try:
                shutil.rmtree(path)
            except Exception as e:
                errors.append(f"Failed to remove {name}: {str(e)}")
                continue

        removed.append(name)

    # Always prune
    await run_exec(["git", "worktree", "prune"], cwd=repo_root)

    return {
        "success": len(errors) == 0,
        "removed": removed,
        "already_clean": already_clean,
        "errors": errors
    }
```

---

## MCP Server Skeleton (FastMCP API)

Using the `FastMCP` high-level API for simpler tool registration with type annotations:

```python
#!/usr/bin/env python3
"""OmniReview MCP Server — worktree and MR data tools for code review."""

import asyncio
import json
import os
import re
import shutil
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("omnireview")

# --- Input Validation (see Security section above) ---

def validate_mr_id(mr_id: str) -> str: ...
def validate_repo_root(repo_root: str) -> str: ...
def validate_branch_name(branch: str) -> str: ...

# --- Safe Command Execution (see Security section above) ---

async def run_exec(args: list, cwd: str, timeout: int = 60): ...

# --- Helper Functions (see Helper Functions section above) ---

def extract_changed_files(diff_text: str) -> list: ...
def parse_commits(log_output: str) -> list: ...
def truncate_diff_if_needed(diff_text: str, line_count: int) -> tuple: ...

# --- Tool Definitions ---

@mcp.tool()
async def fetch_mr_data(mr_id: str, repo_root: str) -> str:
    """Fetch all GitLab MR data (metadata, comments, diff, commits) in a single call.

    Args:
        mr_id: Merge request number (e.g., '136' or '!136')
        repo_root: Absolute path to the git repository root
    """
    result = await _fetch_mr_data(mr_id, repo_root)
    return json.dumps(result, indent=2)

@mcp.tool()
async def create_review_worktrees(mr_id: str, source_branch: str, repo_root: str) -> str:
    """Create 3 isolated git worktrees for OmniReview agents (analyst, codebase, security).

    Args:
        mr_id: Merge request number
        source_branch: MR source branch name (from fetch_mr_data response)
        repo_root: Absolute path to the git repository root
    """
    result = await _create_review_worktrees(mr_id, source_branch, repo_root)
    return json.dumps(result, indent=2)

@mcp.tool()
async def cleanup_review_worktrees(mr_id: str, repo_root: str) -> str:
    """Remove all OmniReview worktrees for a given MR. Always succeeds or reports errors.

    Args:
        mr_id: Merge request number
        repo_root: Absolute path to the git repository root
    """
    result = await _cleanup_review_worktrees(mr_id, repo_root)
    return json.dumps(result, indent=2)

# --- Internal implementations (as defined in Tool Specifications above) ---

async def _fetch_mr_data(mr_id, repo_root): ...
async def _create_review_worktrees(mr_id, source_branch, repo_root): ...
async def _cleanup_review_worktrees(mr_id, repo_root): ...

# --- Entry Point ---

if __name__ == "__main__":
    mcp.run()
```

**Key differences from the low-level Server API:**
- `FastMCP` auto-generates input schemas from type annotations and docstrings
- No manual `list_tools()` or `call_tool()` routing needed
- `@mcp.tool()` decorator handles registration
- `mcp.run()` handles stdio transport automatically
```

---

## How SKILL.md Changes

### Phase 1 (Before)
```
glab auth status
glab mr view {id} -F json
glab mr view {id} -c
glab mr diff {id} --raw
git fetch origin {source_branch}
git log --oneline ...
```

### Phase 1 (After)
```
Call mcp__omnireview__fetch_mr_data(mr_id="{id}", repo_root="{cwd}")
→ Returns structured MR package with all data
```

### Phase 2 (Before)
```
mkdir -p .worktrees
git check-ignore ... || echo ... >> .gitignore
git worktree remove ... (×3)
git worktree prune
git fetch origin ...
git worktree add ... (×3)
cd ... && pwd (×3)
```

### Phase 2 (After)
```
Call mcp__omnireview__create_review_worktrees(
    mr_id="{id}",
    source_branch="{from Phase 1}",
    repo_root="{cwd}"
)
→ Returns {analyst: "/abs/path", codebase: "/abs/path", security: "/abs/path"}
```

### Phase 7 (Before)
```
git worktree remove ... (×3)
rm -rf ... (×3)
git worktree prune
```

### Phase 7 (After)
```
Call mcp__omnireview__cleanup_review_worktrees(mr_id="{id}", repo_root="{cwd}")
→ Returns {removed: [...], success: true}
```

---

## Future Tools (v0.2+)

These are candidates for future expansion, built on the same MCP server:

| Tool | Purpose | Priority |
|------|---------|----------|
| `post_review_summary` | Post the overview comment to MR | High |
| `post_inline_thread` | Post a single inline discussion thread | High |
| `post_full_review` | Combined: summary + all inline threads | High |
| `create_linked_issue` | Create GitLab issue linked to MR | Medium |
| `approve_mr` | Approve the merge request | Medium |
| `check_platform` | Detect if repo is GitLab/GitHub | Medium |
| `parse_diff` | Analyze diff complexity and file changes | Low |
| `list_active_worktrees` | List all OmniReview worktrees currently alive | Low |

---

## Testing Plan

1. **Unit tests** for each tool function (mock subprocess calls)
2. **Integration test** with a real GitLab MR (MR !136)
3. **Error case tests**: invalid MR, no auth, stale worktrees, partial failures
4. **Cleanup reliability**: kill the process mid-review, verify cleanup still works on next run

---

## Dependencies

- Python 3.10+
- `mcp` package (MCP SDK for Python)
- `glab` CLI (already a prerequisite)
- `git` 2.15+ (already a prerequisite)

No additional system dependencies. The MCP server is pure Python with subprocess calls to existing CLI tools.
