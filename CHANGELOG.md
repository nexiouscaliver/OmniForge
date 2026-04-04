# Changelog

All notable changes to OmniReview will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.0] - 2026-03-29

### Fixed
- **Character-based diff truncation** (`MAX_DIFF_CHARS=150K`) — prevents MCP client overflow on large MRs where diff is under 10K lines but over 150K characters
- **Inline discussion type detection** — `fetch_mr_discussions` now uses `DiffNote` note type as fallback when `position` data is missing from GitLab API response, fixing OmniFix misclassifying inline threads as general
- **Fix agent permissions** — OmniFix Phase 4 now instructs `mode: "acceptEdits"` for fix subagent dispatch, preventing permission blocks
- **Pre-commit hook in worktrees** — OmniFix Phase 6 now uses `PRE_COMMIT_ALLOW_NO_CONFIG=1` for worktree commits
- **Working directory drift** — OmniFix Phase 7 now explicitly returns to repo root after cleanup
- **Triage confidence threshold** — triage agent prompt now enforces `NEEDS_HUMAN` for confidence < 70, preventing low-confidence findings from being auto-fixed

### Changed
- `truncate_diff_if_needed` now checks both line count and character count
- OmniReview SKILL.md adds "Large Diff Strategy" section for context-efficient agent dispatch
- Triage agent prompt confidence scale now includes verdict implications
- **SKILL.md context reduction** — phase-specific detail extracted to `./references/` for dynamic loading (omnireview -25%, omnifix -22%). New files: `posting-guide.md`, `approval-guide.md`, `commit-and-post-guide.md`
- False Positive Auto-Reduction rules added to `consolidation-guide.md` (were only in SKILL.md before)

---

## [1.5.0] - 2026-04-01

### Added
- **omnicreate-gitlab** skill — automates GitLab merge request creation via `glab` CLI with auto-populated title/description from commits, draft support, labels, assignees, and more
- `create_gitlab_mr` MCP tool — safe MR creation via MCP server instead of raw bash commands
- 17 new tests for MR creation tool (116 total)

### Changed
- MCP server now exposes 13 tools (was 12)
- README updated with new "Included Skills" section documenting `/omnireview`, `/omnifix`, and `/omnicreate-gitlab`
- Project structure in README and CONTRIBUTING updated to show `skills/omnicreate-gitlab/` directory

---

## [1.3.0] - 2026-03-28

### Added
- **omnifix-gitlab** skill — automated review finding fixer with 7-phase pipeline:
  triage (parallel subagents) → user approval → sequential fix → verify → commit + resolve
- `fetch_mr_discussions` MCP tool — structured fetch of all discussion threads with
  file:line positions, resolved status, replies, and pagination support
- `reply_to_discussion` MCP tool — post replies to specific discussion threads
- `resolve_discussion` MCP tool — resolve/unresolve discussion threads
- `cleanup_omnifix_worktrees` MCP tool — clean up fix worktrees and temp branches
- 3 subagent prompt templates: triage-agent, fix-agent, verify-agent
- 20 new tests for discussion tools and cleanup (92 total)

### Changed
- MCP server now exposes 12 tools (was 8)
- README updated with OmniFix section, expanded tools table, updated roadmap

---

## [1.2.2] - 2026-03-27

### Added
- `create_linked_issue` MCP tool — creates GitLab issues automatically linked to the source MR via `--linked-mr` flag
- 5 new tests for issue creation (72 total)

### Changed
- MCP server now exposes 8 tools (was 7)
- SKILL.md Phase 6 action commands table includes `create_linked_issue`
- Bash fallback for issue creation now includes `--linked-mr` and `--no-editor` flags

### Fixed
- `_get_mr_diff_refs` returns structured error dict with JSON parse safety
- `_post_inline_thread` checks `diff_refs.get("success")` instead of truthiness
- Subprocess decode uses `errors="replace"` for non-UTF-8 resilience

---

## [1.2.1] - 2026-03-26

### Added
- `map_diff_lines` MCP tool — parses unified diffs and returns exact changed line numbers per file, ensuring inline threads always land on valid diff lines
- `fetch_mr_data` now includes `diff_line_map` in its response (line map comes free with MR data)
- 11 new tests for diff line mapping (67 total)

### Changed
- MCP server now exposes 7 tools (was 6)
- README updated with `map_diff_lines` in tools table and roadmap

### Fixed
- `_get_mr_diff_refs` now returns structured error dict instead of `None` with JSON parse safety
- `_post_inline_thread` caller updated to check `diff_refs.get("success")` instead of truthiness (bug: error dict was truthy, causing `KeyError: 'iid'`)
- Subprocess output decode uses `errors="replace"` to prevent crashes on non-UTF-8 characters

---

## [1.2.0] - 2026-03-26

### Added
- 3 new MCP posting tools for cheaper model compatibility:
  - `post_review_summary` — post top-level MR summary comment
  - `post_inline_thread` — post inline discussion thread on exact diff line (auto-fetches SHAs, constructs position data)
  - `post_full_review` — combined: summary + all inline threads in one call
- Marketplace distribution format (`marketplace.json` + `plugins/omnireview/` subdirectory)
- CLAUDE.md for Claude Code development guidance
- 13 new tests for posting tools (56 total)

### Changed
- Restructured repo from flat layout to official Anthropic plugin marketplace format
- SKILL.md Phase 6 action commands now reference MCP posting tools (with bash fallback)
- MCP server now exposes 6 tools (was 3)
- Installation via `claude plugin marketplace add` + `claude plugin install`

### Fixed
- stdin inheritance bug: subprocesses no longer consume MCP JSON-RPC pipe (`stdin=DEVNULL`)
- Dependency resolution: `.mcp.json` uses `uv run --with mcp[cli]` instead of bare `python3`
- Missing `{SOURCE_BRANCH}`/`{TARGET_BRANCH}` placeholders in MR Analyst template
- `.gitignore` now covers `.venv/`, `.worktrees/`, `.DS_Store`

---

## [1.0.0] - 2026-03-26

### Added
- 3 parallel review agents: MR Analyst, Codebase Reviewer, Security Reviewer
- Git worktree isolation for each agent
- Confidence scoring (0-100) with cross-correlation boosting
- 9-option post-review action menu with combined "Full review post"
- Summary comment template (MR overview format)
- Inline discussion thread template (technical, per-finding)
- MCP tool server with 3 tools:
  - `fetch_mr_data` — unified MR data fetch
  - `create_review_worktrees` — atomic 3-worktree creation
  - `cleanup_review_worktrees` — reliable worktree cleanup
- Security-hardened subprocess execution (no shell injection)
- Input validation on all tool parameters
- Subprocess timeouts (30-120s)
- Large diff auto-truncation (10K line limit)
- "Small MR Trap" defense against skipping the full review process
- Comprehensive rationalization table for common review shortcuts
- Plugin format: `.claude-plugin/plugin.json` for marketplace compatibility
- 40 unit tests with mocked subprocess calls
- PLUGIN_CONVERSION_GUIDE.md for future publishing reference
