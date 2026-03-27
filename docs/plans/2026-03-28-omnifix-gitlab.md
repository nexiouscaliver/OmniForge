# omnifix-gitlab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `omnifix-gitlab` skill and 4 new MCP tools that automate fixing review findings on GitLab MRs — triage with parallel subagents, sequential fixing, verification, and discussion thread resolution.

**Architecture:** Two phases — MCP tools first (Tasks 1-5), then skill + templates (Tasks 6-8). Tools are the foundation (testable in isolation), skill orchestrates the 7-phase pipeline using the tools.

**Tech Stack:** Python 3.10+ (FastMCP), `glab` CLI, `asyncio`, Markdown skill files

**Spec:** `docs/specs/2026-03-28-omnifix-gitlab-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `plugins/omnireview/tools/omnireview_mcp_server.py` | Modify | Add 4 new MCP tools (~200 lines) |
| `plugins/omnireview/tests/test_discussions.py` | Create | Tests for fetch_mr_discussions, reply_to_discussion, resolve_discussion |
| `plugins/omnireview/tests/test_omnifix_cleanup.py` | Create | Tests for cleanup_omnifix_worktrees |
| `plugins/omnireview/skills/omnifix-gitlab/SKILL.md` | Create | 7-phase fix workflow |
| `plugins/omnireview/skills/omnifix-gitlab/references/triage-agent-prompt.md` | Create | Triage subagent template |
| `plugins/omnireview/skills/omnifix-gitlab/references/fix-agent-prompt.md` | Create | Fix implementer template |
| `plugins/omnireview/skills/omnifix-gitlab/references/verify-agent-prompt.md` | Create | Verification template |
| `README.md` | Modify | Add omnifix-gitlab section |

---

## Task 1: `fetch_mr_discussions` MCP tool + tests (TDD)

**Files:**
- Modify: `plugins/omnireview/tools/omnireview_mcp_server.py`
- Create: `plugins/omnireview/tests/test_discussions.py`

This is the most complex new tool — parses GitLab's discussion API response into structured findings.

- [ ] Write tests for `_fetch_mr_discussions`:
  - `test_success` — mock `glab api --paginate` response with 2 inline + 1 general discussion. Verify: correct `type` classification, `file_path` and `line_number` extracted from position data, `body` from first note, `replies` from subsequent notes, `total/unresolved/resolved` counts.
  - `test_filters_system_notes` — mock response with `system: true` notes. Verify they are excluded from results.
  - `test_inline_vs_general` — mock `individual_note: true` (general) and `individual_note: false` with position (inline). Verify correct type assignment.
  - `test_skips_omnireview_summary` — mock a general discussion whose body starts with `## OmniReview`. Verify it appears as `type: "general"` with `resolvable: false`.
  - `test_empty_discussions` — MR with no discussions. Verify `discussions: [], total: 0`.
  - `test_api_failure` — mock `glab api` returning non-zero. Verify error response.
  - `test_invalid_mr_id` — pass "abc". Verify `validation_error`.
- [ ] Run tests — confirm they fail
- [ ] Implement `_fetch_mr_discussions` in `omnireview_mcp_server.py`:
  - Validate inputs
  - Fetch MR IID via `glab mr view {mr_id} -F json` (reuse `_get_mr_diff_refs` pattern for IID)
  - Call `glab api projects/:fullpath/merge_requests/{iid}/discussions --paginate`
  - Parse JSON response into structured discussions:
    ```python
    for disc in raw_discussions:
        notes = [n for n in disc.get("notes", []) if not n.get("system", False)]
        if not notes:
            continue
        first_note = notes[0]
        position = first_note.get("position", {})
        discussions.append({
            "id": disc["id"],
            "resolvable": disc.get("resolvable", False),
            "resolved": disc.get("resolved", False),
            "type": "inline" if position and position.get("new_path") else "general",
            "file_path": position.get("new_path"),
            "line_number": position.get("new_line"),
            "body": first_note.get("body", ""),
            "author": first_note.get("author", {}).get("username", ""),
            "created_at": first_note.get("created_at", ""),
            "replies": [{"author": n["author"]["username"], "body": n["body"], "created_at": n["created_at"]} for n in notes[1:]],
        })
    ```
  - Count total/unresolved/resolved
- [ ] Add FastMCP wrapper `fetch_mr_discussions(mr_id, repo_root)`
- [ ] Run tests — confirm all pass
- [ ] Commit: `feat: add fetch_mr_discussions MCP tool with structured parsing`

---

## Task 2: `reply_to_discussion` MCP tool + tests (TDD)

**Files:**
- Modify: `plugins/omnireview/tools/omnireview_mcp_server.py`
- Modify: `plugins/omnireview/tests/test_discussions.py` (append)

- [ ] Write tests:
  - `test_reply_success` — mock POST returning 200. Verify `success: true, action: "reply_posted"`.
  - `test_reply_failure` — mock POST returning 403. Verify `error_type: "post_failed"`.
  - `test_reply_empty_body` — empty body string. Verify `validation_error`.
  - `test_reply_invalid_mr_id` — verify `validation_error`.
- [ ] Run tests — confirm they fail
- [ ] Implement `_reply_to_discussion`:
  - Validate `mr_id`, `repo_root`, `body` non-empty
  - Fetch IID via `_get_mr_diff_refs`
  - Call `glab api projects/:fullpath/merge_requests/{iid}/discussions/{discussion_id}/notes --method POST --raw-field "body={body}"`
- [ ] Add FastMCP wrapper
- [ ] Run tests — confirm all pass
- [ ] Commit: `feat: add reply_to_discussion MCP tool`

---

## Task 3: `resolve_discussion` MCP tool + tests (TDD)

**Files:**
- Modify: `plugins/omnireview/tools/omnireview_mcp_server.py`
- Modify: `plugins/omnireview/tests/test_discussions.py` (append)

- [ ] Write tests:
  - `test_resolve_success` — mock PUT returning 200. Verify `success: true, resolved: true`.
  - `test_unresolve_success` — pass `resolved=false`. Verify `resolved: false` in response.
  - `test_resolve_failure` — mock PUT returning 404. Verify `error_type: "resolve_failed"`.
  - `test_resolve_invalid_mr_id` — verify `validation_error`.
- [ ] Run tests — confirm they fail
- [ ] Implement `_resolve_discussion`:
  - Validate `mr_id`, `repo_root`
  - Fetch IID via `_get_mr_diff_refs`
  - Call `glab api projects/:fullpath/merge_requests/{iid}/discussions/{discussion_id} --method PUT --raw-field "resolved={true|false}"`
- [ ] Add FastMCP wrapper
- [ ] Run tests — confirm all pass
- [ ] Commit: `feat: add resolve_discussion MCP tool`

---

## Task 4: `cleanup_omnifix_worktrees` MCP tool + tests (TDD)

**Files:**
- Modify: `plugins/omnireview/tools/omnireview_mcp_server.py`
- Create: `plugins/omnireview/tests/test_omnifix_cleanup.py`

- [ ] Write tests:
  - `test_cleanup_fix_worktree` — create `.worktrees/omnifix-{mr_id}` dir. Mock git commands. Verify removed.
  - `test_cleanup_triage_worktrees` — create `.worktrees/omnifix-triage-{mr_id}-0` and `-1` dirs. Verify both removed.
  - `test_cleanup_temp_branch` — verify `git branch -D omnifix-temp-{mr_id}` is called.
  - `test_already_clean` — no dirs exist. Verify `already_clean` list.
  - `test_invalid_mr_id` — verify `validation_error`.
- [ ] Run tests — confirm they fail
- [ ] Implement `_cleanup_omnifix_worktrees`:
  - Validate inputs
  - Remove `.worktrees/omnifix-{mr_id}` (fix worktree)
  - Glob for `.worktrees/omnifix-triage-{mr_id}-*` and remove each
  - Delete temp branch `omnifix-temp-{mr_id}` (ignore error if doesn't exist)
  - `git worktree prune`
- [ ] Add FastMCP wrapper
- [ ] Run ALL tests (entire test suite) — confirm all pass
- [ ] Commit: `feat: add cleanup_omnifix_worktrees MCP tool`

---

## Task 5: Verify MCP server registers all tools

**Files:**
- None modified — verification only

- [ ] Run full test suite: `cd plugins/omnireview && python -m pytest tests/ -v`
- [ ] Verify MCP server starts and lists all tools:
  ```bash
  echo '...(initialize + tools/list)...' | uv run --with "mcp[cli]" python3 plugins/omnireview/tools/omnireview_mcp_server.py
  ```
  Expected: 12 tools (was 8): existing 8 + fetch_mr_discussions + reply_to_discussion + resolve_discussion + cleanup_omnifix_worktrees
- [ ] Commit: `chore: verify 12 MCP tools registered`

---

## Task 6: Create omnifix-gitlab SKILL.md

**Files:**
- Create: `plugins/omnireview/skills/omnifix-gitlab/SKILL.md`

- [ ] Create directory: `mkdir -p plugins/omnireview/skills/omnifix-gitlab/references`
- [ ] Write SKILL.md with frontmatter:
  ```yaml
  ---
  name: omnifix-gitlab
  description: Use when fixing review findings on a GitLab MR, resolving inline discussion threads, applying code review suggestions, or when asked to fix issues from an OmniReview report
  argument-hint: <mr-number>
  allowed-tools: [Read, Glob, Grep, Bash, Agent, Write, Edit]
  ---
  ```
- [ ] Write full 7-phase workflow following `omnireview-gitlab` structure:
  - Phase 1 (Gather): `fetch_mr_discussions` + `fetch_mr_data` MCP tools, filtering logic
  - Phase 2 (Triage): subagent dispatch strategy, worktree creation (detached for read-only), grouping logic
  - Phase 3 (Approve): triage results presentation, user options (fix all VALID / select / include NEEDS_HUMAN / cancel), auto-resolve option (default NO), commit strategy option (single/per-fix)
  - Phase 4 (Fix): writable worktree creation (`-b omnifix-temp-{mr_id}`), single subagent dispatch, test discovery order
  - Phase 5 (Verify): verification subagent, NEEDS_REWORK loop (max 2 iterations)
  - Phase 6 (Commit + Post): race condition check (fetch + compare before push), reply + optional resolve, summary comment
  - Phase 7 (Cleanup): `cleanup_omnifix_worktrees` MCP tool
  - Error handling table, Red Flags, Never/Always rules
  - 25-finding cap with user override
- [ ] Commit: `feat: add omnifix-gitlab SKILL.md (7-phase fix workflow)`

---

## Task 7: Create subagent prompt templates

**Files:**
- Create: `plugins/omnireview/skills/omnifix-gitlab/references/triage-agent-prompt.md`
- Create: `plugins/omnireview/skills/omnifix-gitlab/references/fix-agent-prompt.md`
- Create: `plugins/omnireview/skills/omnifix-gitlab/references/verify-agent-prompt.md`

- [ ] Write `triage-agent-prompt.md`:
  - Header: "# Triage Agent (OmniFix)"
  - Placeholders: {MR_ID}, {MR_TITLE}, {WORKTREE_PATH}, {FINDINGS_FOR_THIS_AGENT}
  - Instructions: read finding → read file at indicated lines → trace context → decide VALID/INVALID/NEEDS_HUMAN → propose fix if VALID
  - Output format: structured JSON verdict per finding
  - Adversarial stance: "don't accept findings just because someone posted them"

- [ ] Write `fix-agent-prompt.md`:
  - Header: "# Fix Agent (OmniFix)"
  - Placeholders: {MR_ID}, {MR_TITLE}, {WORKTREE_PATH}, {APPROVED_FIXES_JSON}, {TEST_COMMAND}
  - Instructions: apply fixes sequentially in file order → run tests → self-review → report
  - Rule: "Do NOT commit — the main agent handles that"
  - Status codes: DONE / DONE_WITH_CONCERNS / BLOCKED

- [ ] Write `verify-agent-prompt.md`:
  - Header: "# Verification Agent (OmniFix)"
  - Placeholders: {MR_ID}, {MR_TITLE}, {WORKTREE_PATH}, {FINDINGS}, {GIT_DIFF}, {TEST_COMMAND}
  - Instructions: verify each change addresses its finding → check for regressions → run tests → assess style
  - Verdict: APPROVED / NEEDS_REWORK with specifics

- [ ] Verify all `./references/` paths in SKILL.md resolve to actual files
- [ ] Commit: `feat: add omnifix-gitlab subagent prompt templates`

---

## Task 8: Update README + CHANGELOG + push

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `plugins/omnireview/.claude-plugin/plugin.json` (version bump)

- [ ] Add "OmniFix" section to README after the OmniReview section:
  - Brief description of omnifix-gitlab
  - The 7-phase flow diagram (simplified)
  - Usage: `/omnifix-gitlab 136`
  - Note: complements omnireview-gitlab (review → fix cycle)

- [ ] Update README MCP tools table: 12 tools (add fetch_mr_discussions, reply_to_discussion, resolve_discussion, cleanup_omnifix_worktrees)

- [ ] Update README roadmap: mark "Automated fix skill (omnifix-gitlab)" as complete

- [ ] Add CHANGELOG entry for the new version

- [ ] Bump version in `plugin.json`

- [ ] Run full test suite one last time
- [ ] Push to GitHub
- [ ] Commit: `docs: add omnifix-gitlab to README, update changelog`

---

## Verification Checklist

1. `cd plugins/omnireview && python -m pytest tests/ -v` — all tests pass
2. MCP server lists 12 tools
3. `ls plugins/omnireview/skills/omnifix-gitlab/` shows SKILL.md + references/
4. `ls plugins/omnireview/skills/omnifix-gitlab/references/` shows 3 template files
5. `grep "omnifix" plugins/omnireview/skills/omnifix-gitlab/SKILL.md` — MCP tools referenced correctly
6. `grep "./references/" plugins/omnireview/skills/omnifix-gitlab/SKILL.md` — all paths resolve
7. README documents omnifix-gitlab with usage instructions
