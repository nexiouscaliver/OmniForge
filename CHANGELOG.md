# Changelog

All notable changes to OmniReview will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
