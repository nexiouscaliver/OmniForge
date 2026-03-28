# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

OmniReview is a Claude Code plugin distributed as its own marketplace. It contains two skills:
- **omnireview-gitlab** — dispatches 3 parallel AI review agents in isolated git worktrees to adversarially review GitLab MRs
- **omnifix-gitlab** — automates fixing review findings with parallel triage subagents, sequential fixing, verification, and thread resolution

## Repository Layout

This repo has two layers: the **marketplace root** and the **plugin** inside it.

```
/                                    ← Marketplace root
  .claude-plugin/marketplace.json       ← Points to ./plugins/omnireview
  plugins/omnireview/                   ← THE PLUGIN
    .claude-plugin/plugin.json
    .mcp.json                           ← Spawns the MCP server via uv
    skills/
      omnireview-gitlab/                ← Review skill (7-phase review workflow)
        SKILL.md
        references/                     ← 5 files: 3 agent prompts + consolidation guide + posting guide
      omnifix-gitlab/                   ← Fix skill (7-phase fix workflow)
        SKILL.md
        references/                     ← 5 files: 3 agent prompts + approval guide + commit/post guide
    tools/omnireview_mcp_server.py      ← Python MCP server (FastMCP, 12 tools)
    tests/                              ← 99 unit tests
```

The marketplace wrapper exists because `claude plugin marketplace add` requires plugins in subdirectories — it doesn't support a plugin at repo root.

## Commands

### Run tests
```bash
cd plugins/omnireview && python -m pytest tests/ -v
```

### Run a single test
```bash
cd plugins/omnireview && python -m pytest tests/test_discussions.py::TestFetchMrDiscussions::test_success -v
```

### Start MCP server manually (for testing)
```bash
uv run --with "mcp[cli]" python3 plugins/omnireview/tools/omnireview_mcp_server.py
```

### Load plugin locally (without installing)
```bash
claude --plugin-dir plugins/omnireview
```

### Install as marketplace plugin
```bash
claude plugin marketplace add https://github.com/nexiouscaliver/OmniReview.git
claude plugin install omnireview@omnireview-marketplace
```

## Architecture

### Two Skills

**omnireview-gitlab** (review): Gather → Isolate → Dispatch 3 agents → Consolidate → Report → Act → Cleanup
**omnifix-gitlab** (fix): Gather → Triage (parallel) → Approve → Fix → Verify → Post → Cleanup

Both skills support MCP tools (plugin install) with bash fallback (personal skill install).

### MCP Server (`tools/omnireview_mcp_server.py`)

Single-file FastMCP server with 12 tools. The structure follows a pattern:

- **Validators** (`validate_mr_id`, `validate_repo_root`, `validate_branch_name`) — called at the top of every tool function
- **`run_subprocess`** (aliased as `run_exec`) — all external commands go through this. Uses `create_subprocess_exec` (argument list, never shell), `stdin=DEVNULL` (prevents MCP pipe inheritance), and `asyncio.wait_for` timeout
- **Helpers** (`extract_changed_files`, `parse_commits`, `truncate_diff_if_needed`, `parse_diff_line_map`) — pure functions for parsing
- **Tool implementations** — async `_function` functions that do the actual work, return dicts
- **FastMCP wrappers** — thin `@mcp_server.tool()` decorated functions that call internal implementations and `json.dumps` the result
- **Entry point** — `mcp_server.run()` at the bottom

### 12 MCP Tools

| Tool | Used By |
|------|---------|
| `fetch_mr_data` | Review + Fix |
| `create_review_worktrees` | Review |
| `cleanup_review_worktrees` | Review |
| `post_full_review` | Review |
| `post_review_summary` | Review + Fix |
| `post_inline_thread` | Review |
| `create_linked_issue` | Review + Fix |
| `map_diff_lines` | Review + Fix |
| `fetch_mr_discussions` | Fix |
| `reply_to_discussion` | Fix |
| `resolve_discussion` | Fix |
| `cleanup_omnifix_worktrees` | Fix |

### Key Invariant: `./references/` Paths

Each SKILL.md references its supporting files as `./references/filename.md`. These paths are relative to the skill directory (where SKILL.md lives), NOT relative to the repo root. If you move SKILL.md, the references break.

## Testing

Tests mock `run_exec` via `unittest.mock.patch("omnireview_mcp_server.run_exec")`. Test files use `sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))` to import from the tools directory.

When adding new MCP tools: write the `_internal` implementation function first with tests mocking `run_exec`, then add the thin `@mcp_server.tool()` wrapper.

## Security Constraints

- All subprocess calls MUST use `create_subprocess_exec` (never `create_subprocess_shell`)
- All subprocess calls MUST include `stdin=asyncio.subprocess.DEVNULL`
- All tool functions MUST validate inputs before any subprocess call
- The MCP server MUST NOT write non-JSON-RPC content to stdout
- Posted MR comments MUST NOT contain AI attribution

## Git Workflow

- Do not push to origin without explicit permission
- Use Shahil Kadia as commit author name
- No Claude/AI attribution in commits or MR comments
- Use `glab` for GitLab operations (never `gh`)
