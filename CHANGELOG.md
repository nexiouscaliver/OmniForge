# Changelog

All notable changes to OmniForge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.4.0] - 2026-09-06

Round-2 latency release — deterministic pre-dispatch, judgment-only adjudication, the fix-brief note, and a cost-weighted 3-agent partition. R2-E A/B validation verdict: **GO** (full evidence: `round2-validation.md` in the engine repo's speed-campaign artifacts).

### Added
- **`omni_prepare.py` deterministic pre-dispatch (R2-A, PR #34)** — ONE stdlib-only invocation runs the entire Phase-1 lead-in (gather file via the fetcher subprocess, partition, digest, per-agent dispatch briefs) and emits `prepare.json`, one-line machine receipts, and `phases.jsonl` `prepare` rows (measured 3.73 s / 4.05 s — O(seconds) at any MR size, replacing the improvised size-dependent gather behind the **827 s fleet-median pre-dispatch → 265 s raw / ~193 s production-equivalent** on the R2-E MR). Exit codes are a soft-fail contract: any nonzero exit falls back to the improvised Phase-1 path immediately — no retry, no STOP (proven end-to-end by the R2-E force-exit-1 probe: fallback invoked 9 s after the failure, run completed engine exit 0 with a full report)
- **`omni_adjudicate.py` mechanical pre-pass + judgment-only Phase 5 (R2-B, PR #33)** — the worklist's mechanical rows (single-agent autos, near-dup merges, prior matches) are decided script-final without the LLM; only cross-finding and validity rows reach judgment. Phase-5 adjudication on the R2-E real MR: **239 s / 22 turns — best ever measured** (fleet median 461 s / 40 turns; W4 24–31; prior production best 20 turns), with 17 of 26 rows auto-decided and a 9-row judgment core
- **Fix-brief note (R2-P, PR #32)** — the poster's last artifact is a self-contained "OmniForge fix brief — paste this into your coding agent" general note (template v1): frozen guard clause first, MR-intent line, then one line per new adjudicated finding (`FIXED in <short-sha> —` / `SKIPPED —` / `QUESTION —`) linking its exact `#note_<id>` thread. R2-E gate 5: 20/20 links map 1:1 to posted artifacts (0 dead links, set-equality with the new-adjudicated set); >25-findings cap exemption unit-covered; poster stdout gains `fix_brief: true`

### Changed
- **Cost-weighted 3-agent partition (R2-D, PR #35)** — `omni_partition.py` balances reviewer assignments by a per-file cost model (not file count) with a 1.25× cap and security spill: real-MR per-agent balance **1288/1296/417‰ → 1000/1000/1000‰** (76/75/76 cost units) — deterministic, ending the 2× reviewer wall skew; dispatch briefs trimmed **−51.4 %** (cross-cutting pointer + stats-bullet trim, with tripwire tests against over-trimming)
- **SKILL.md Phases 1–3 rewritten script-first** — `omni_prepare.py` is the primary lead-in; `omni_fetch_mr.py` executes only inside prepare and at the two sanctioned `--verify-head` checkpoints (R2-E gate 2: zero double-fetch on the knob-on path)
- Test suite 560 + 128 subtests (was 383 + 19 at 3.3.3)
- Version bumped to 3.4.0

### Validation (R2-E A/B — frozen head of MR !21, A = 3.3.3 dry vs B = Round-2 real)
- **Verdict GO** — every functional, safety, and quality gate passed: prepare-first structure with machine receipts, no double-fetch, script-final auto rows, 8/8 planned threads anchored first-try, poster `failures: 0`, zero sleeps, zero MCP on the data path (MCP remains only for the Phase-2/7 worktree lifecycle), waiter unchanged; findings 21 vs 21 (≥70 % of baseline), every verdict flip attributed to reviewer nondeterminism — 0 attributable to the Round-2 levers
- **Fallback probe PASS** (gate 6) — stubbed `omni_prepare.py` exiting 1 → improvised-path fallback in 9 s, no retry/STOP; run completed engine exit 0, report 23,806 bytes, harness SUCCESS
- **Two turn-count target misses disclosed** — pre-dispatch 45 turns vs ≤10, adjudication 22 turns vs ≤12: calibration findings, not lever regressions (harness-only token friction ≈12 turns, retrospective re-render ≈8 turns absent on first reviews, agent inspection verbosity ≈25 turns — no measured arm, including the 3.3.3 baseline at 42 turns, can hit ≤10 under the local harness); **R2-F fleet telemetry is the production confirmation** — watch `phases.jsonl` `prepare` rows and Phase-5 walls
- 3.4.x backlog (disclosed): `ADJ_ARGS` snippet ↔ argparse first-call hiccup (2-turn self-correcting tax), retrospective prepare double-gather (+4 s), fix-brief >25-cap path awaits its first real >25-findings MR

---

## [3.3.3] - 2026-09-04

Fix release — resolves the three findings from the 3.3.2 production canary (re-review of MR !1388, pure-deletion +0/−16).

### Fixed
- **Poster full-path project 404** — `omni_post_review.py` URL-encodes non-numeric `--project` values on EVERY request path it builds (guard notes GET, refs GET, summary/thread/reply/note POSTs) via the shared `omni_glab_api.encode_project`; the CLI help now accepts and documents the bare full path. Production motive (canary, session e18dbabb): `omni_post_review.py --project regenai-gitlab/regenai-digital/cleo` → guard GET 404 — the FETCHER got `encode_project` in 3.3.2 but the poster never did; the run recovered only by switching to the numeric ID. Live-proven first-try on scratch MR !21 with the full path (note-only batch, exit 0, `notes: 1`, note re-fetched by id then deleted 204)

### Added
- **Deletion-MR / unanchorable-loci pre-filter (SKILL.md Phase 4 + posting-guide)** — BEFORE building the findings payload, check the gather data for anchorability: an MR with zero added lines (pure-deletion, e.g. +0/−16) or a finding whose locus has no anchorable line plans as a note entry or a reply on the matching prior thread — never as a thread entry. Production motive (canary, MR !1388): 4 inline threads planned on a pure-deletion MR → GitLab `400 Bad request — Note {:line_code=>["can't be blank"]}` → fail-fast aborted the batch (correct) but the threads were burned; the poster docstring now says the `line_code`-400 remedy is planning (note/reply), not retrying
- **Reply-preference routing rule (SKILL.md Phase 4)** — follow-ups to an existing OmniForge thread MUST be posted as replies on that thread (`reply_to_thread_id`); note entries are ONLY for genuinely anchorless NEW findings with no existing thread — never substitute a note where a reply belongs. Production motive (canary): two prior-thread follow-ups ("Prior thread X is confirmed…") posted as top-level NOTES because the note path had become the path of least resistance

### Changed
- **`encode_project` is ONE shared helper** — moved from `omni_fetch_mr.py` into `omni_glab_api.py` (same semantics: numeric digits-only passthrough, already-%-encoded passthrough, else `quote(safe="")`); the fetcher now calls the shared helper (behavior identical — its 3 encoding tests unchanged and green) and the poster uses it on every path builder
- Test suite 383 + 19 subtests (was 376 + 19)
- Version bumped to 3.3.3

### Deferred to 3.3.4+
- line_range positions (needs nested-param dev validation), adjudication-turns lever, dead-subagent watchdog automation — unchanged from 3.3.2

---

## [3.3.2] - 2026-09-04

### Added
- **Note entries on `omni_post_review.py`** — findings objects carrying ONLY `{"body": ...}` (no `file_path`/`line_number`) post as top-level MR notes (execution order: summary → threads → replies → notes), counted in the additive stdout key `notes`; mixed batches fine; a body-only entry that also carries anchor keys is an ambiguous-shape usage error (exit 2 — never silently skipped). Replaces the raw-glab general-notes path: `glab api --input -` silently drops note bodies — production !1388 lost 5/5 MR-meta notes to it. Live-proven on scratch MR !21: note-only batch (no `--summary`, no `--skip-summary`) exit 0 with `notes: 1`, the note re-fetched by id, then deleted (204)
- **`--host` on `omni_post_review.py`** — same resolution order as the fetch script (flag > GITLAB_HOST > CI_API_V4_URL > https://gitlab.com), threaded through EVERY API call including the guard's notes listing and the note posts (parity with `omni_fetch_mr.py`)
- **Auto-summary-skip for no-new-thread batches** — `--summary` is required ONLY when the batch has at least one new-thread entry; reply-only and note-only batches proceed without it (implied skip: no summary, guard not evaluated — same as reply-only today). Removes the production friction of reply-only batches dying on argparse exit 2 (seen twice in production, one wasted turn each); `--skip-summary` keeps its current meaning

### Changed
- **Duplicate-summary guard paginates** — the guard's notes listing now goes through `omni_glab_api.get_all` (per_page=100, page-following), so a busy MR (100+ notes) can no longer hide an OmniForge summary beyond page 1; a single page still costs exactly ONE notes GET (pinned by test)
- **`omni_fetch_mr.py` accepts bare full-path projects** — non-numeric `--project` values are URL-encoded (`urllib.parse.quote(safe="")`) on every endpoint the script builds; numeric IDs and pre-encoded values pass through unchanged, and the CLI help says so. Production motive: the 3.3.1 A/B arm passed `regenai-gitlab/regenai/regenai-base` unencoded → 3× HTTP 404 ≈ 72 s before retreating to the numeric ID. Live-proven first-try on MR !21 with the full path (6 API calls, 4.1 s)
- **SKILL.md + posting-guide document note entries** — Phase 6 posting instructions and the guide's findings-shape section name note entries as the poster's general-notes path (with the `glab api --input -` body-drop hazard as the reason); Phase 3 gains the stalled-subagent rule: attempt exactly ONE SendMessage resume before harvesting partials — never loop
- Test suite 376 + 19 subtests (was 362 + 19)
- Version bumped to 3.3.2

### Fixed
- **Headless MCP −32000** — the `.mcp.json` launch arg is pinned to `mcp[cli]>=1.0.0,<2.0.0`: the unpinned `uv run --with mcp[cli]` resolves mcp 2.x, where `from mcp.server.fastmcp import FastMCP` dies at import and every tool call returns −32000 (diagnosed end-to-end in 3.3.1 with this exact pin proven locally; now shipped under the operator's keep-MCP decision)

### Deferred to 3.3.3+
- line_range positions (needs nested-param dev validation), adjudication-turns lever, dead-subagent watchdog automation

---

## [3.3.1] - 2026-09-03

### Added
- **`omni_fetch_mr.py` one-shot MR gather** (`skills/omnireview-gitlab/scripts/`) — ONE stdlib-only invocation gathers everything review Phase 1 needs into ONE JSON file (MR metadata, paginated diffs, unified-diff headers synthesized where the API omits them, diff_line_map, commits, discussions envelope, versions, notes) with bounded retry/backoff (2 s / 4 s on 5xx/429/network, fail-fast on 400/401/403/404), atomic write, and a one-line stdout JSON receipt (`elapsed_ms`, `api_calls`); replaces the improvised dozens-of-calls Phase-1 gather
- **`--verify-head` mid-run STOP guard** on `omni_fetch_mr.py` + the deterministic STOP protocol pinned in SKILL.md (Phase 3 after the waiter, and again immediately before Phase 6 posting): a moved `diff_refs.head_sha` → exit 4, no re-partition/re-dispatch/re-gather, one flat top-level addendum note, no inline threads on stale anchors — fixes the !1403 class (3 passes / 9 dispatches, watchdog kill, nothing posted)
- **Gather-file shims** — `omni_partition.py` and `omni_digest.py` detect the gather file's shape (`data` + `discussions` at top level) and operate on the embedded envelope; legacy single-/two-file invocations byte-identical
- **`--skip-summary` on `omni_post_review.py`** — threads/replies-only resume path for mid-batch failures (`--skip-summary --force`), closing the guide's old unanchored raw-command escape hatch
- **`omni_glab_api.py` shared direct REST transport** (`skills/omnireview-gitlab/scripts/`) — one tested host/token/auth/retry/pagination contract (stdlib urllib, Bearer auth, token redaction) used by both scripts

### Changed
- **`omni_post_review.py` posts via the direct GitLab Discussions API** — every `glab` subprocess call replaced with `omni_glab_api.request`; inline threads carry the full documented `position[...]` payload, so every thread posts ANCHORED first-try (live-proven on scratch MR !21: the probe thread's first note carries `position.new_path`; see `w4-glab-io-validation.md`); all 3.3.0 poster contracts preserved (exit codes 0/1/2/3, stdout JSON + `elapsed_ms`, duplicate-summary guard, reply routing, `--dry-run` executes nothing and works without a token, refs fetched once per invocation)
- **SKILL.md is script-first** — Phase 1 names `omni_fetch_mr.py` as the primary gather (MCP fetch demoted to optional-interactive); posting-guide names the shipped poster script as the Implementation path (MCP posting tools optional in interactive installs)
- Test suite 362 (was 325)
- Version bumped to 3.3.1

### Fixed
- **Unanchored inline threads** — root cause: `glab api --raw-field "position[base_sha]=..."` drops nested `position[…]` keys, so threads landed as unanchored general notes (measured ~209 s / 27 turns of delete+repost anchor repair per review); the direct-API transport sends literal bracket keys form-encoded and the guide's unanchored raw fallback command is removed
- **Headless MCP −32000 diagnosed (record-only)** — the `.mcp.json` launch line `uv run --with mcp[cli]` resolves the latest mcp (2.x), where `from mcp.server.fastmcp import FastMCP` dies at import; proven end-to-end, and a one-line pin (`mcp[cli]>=1.0.0,<2.0.0`) fixes it locally — NOT shipped in 3.3.1 (operator keep/drop decision pending; the standalone script path is primary either way, so headless runs are unaffected). Full evidence: `w4-mcp-diagnosis.md`
- posting-guide's trailing "MCP `post_full_review` remains the recommended primary" claim removed — contradicted the script-first Implementation line

### A/B
- A/B e2e (same MR !21, same head, both arms real-posting, inter-arm reset, w4 arm first): total wall **1335 s vs 1380 s** (−45 s, within single-pair noise); gather = **6 API calls / 4.3 s** once parameterized vs 5 improvised calls + 7 inline heredocs (~104 s); posting = **~48 s** (dry-run validation + one 20.8 s real post) vs ~86 s + an ~80 s token detour + 1 failed post; **threads anchored first-try 24/24 (100%) vs 0/30** (3.3.0 posted every finding as an unanchored top-level note and never repaired — silent anchor loss, worse than the known delete+repost loop); improvised /tmp executables 0 in both arms; sleeps 0 (w4, main + all subs) vs 2 in one 3.3.0 subagent; STOP-guard `--verify-head` ran at both checkpoints, exit 0 both. Honest misses: 3 failed fetch invocations (~72 s — unencoded full-path 404s + a token detour) before the run settled on the numeric project ID (3.3.2 candidate: accept/URL-encode full project paths); adjudication turns 31 vs 24 (the ≤12 target remains unmet — unchanged lever, not a W4 regression). Full tables: `w4-glab-io-validation.md`

---

## [3.3.0] - 2026-09-02

### Added
- **Structured findings JSON contract + `omni_validate_findings.py`** (`skills/omnireview-gitlab/scripts/`) — each reviewer agent emits a machine-readable findings block; the validator checks all three blocks and passes malformed output through verbatim (never fails the run)
- **`omni_consolidate.py` deterministic consolidation** (`skills/omnireview-gitlab/scripts/`) — merges only near-identical findings (same file+lines+category, Jaccard ≥ 0.30); same-locus non-identical pairs classify as clusters, not merges, with per-agent verbatim entries in the worklist; corroboration is recorded as metadata; confidence values are never adjusted or recomputed; emits one 6-section single-pass adjudication worklist; `--prior` retrospective guard marks prior-matched loci `already_adjudicated` — prior posted findings are authoritative and new input is routed as replies on their existing threads
- **`omni_post_review.py` shipped posting fallback** (`skills/omnireview-gitlab/scripts/`) — standalone `glab`-based posting with retry/backoff (2 s / 4 s on 5xx/429), duplicate-summary guard that refuses to post a second default `## OmniForge` summary, and `--reply-to` / `reply_to_thread_id` thread routing; no MCP dependency
- **`omni_partition.py` diff load balancing** (`skills/omnireview-gitlab/scripts/`) — security-affinity assignment first, then greedy balancing across the codebase and analyst reviewers; plus reviewer-brief tightening and the "Never sleep-poll — not even sub-60 s sleeps" rule
- **`omni_digest.py` context digest** (`skills/omnireview-gitlab/scripts/`) — script-built digest where bot artifacts are carried byte-verbatim, human prose is capped, and diffs are reduced to hunk headers; retrospective wiring in Phases 1/3/4 replaces raw comment dumps

### Changed
- **One-dispatch-per-reviewer rule** in the three reviewer briefs — fixes the !1360 9-vs-3 dispatch-file churn (A/B retrospective arm: `sub_count` == 3, and 3 prior loci marked `already_adjudicated` with replies routed on recorded threads)
- **A/B e2e (same MR, same head, same provider hour):** tail −28.2% (339.5 → 243.7 s, real — beyond the ±75 s single-pair noise band), output tokens −24.4%; adjudication turns 20 vs the ≤12 target — MISS, stated honestly (per-call api time down 32.5 → 10.9 s: the single-pass worklist produces more, much shorter turns)
- Test suite 325 + 9 (was 231)
- Version bumped to 3.3.0

---

## [3.2.0] - 2026-09-01

### Added
- **`omni_wait.py` chunked completion waiter** (`skills/omnireview-gitlab/scripts/`) — waits on the 3 reviewer subagents' transcripts with exit-code semantics (0 = all terminal, 3 = still running, 2 = degraded), a one-line JSON per-agent status, mtime-stability fallback when the `Status: DONE` marker is absent, dead-agent stall detection with partial harvest, a scan-dir fallback for unavailable output_file paths, and per-agent report files written via `--reports-dir`

### Changed
- **omnireview-gitlab Phase 3 completion detection rewritten** — the waiter's exit code is now the sole completion authority; improvised `sleep`-polling and hand-written collect/wait helper scripts are explicitly forbidden; background-agent task notifications are informational only
- Degraded runs (stalled/missing agents) proceed to consolidation with an explicit "coverage degraded (N/3 reviewers)" report note naming the missing perspectives
- Version bumped to 3.2.0

---

## [2.0.0] - 2026-04-05

### BREAKING CHANGES
- **Project renamed from OmniReview to OmniForge** — reflects the full MR lifecycle toolkit (review, fix, create)
- Plugin name: `omnireview` → `omniforge`
- Plugin directory: `plugins/omnireview/` → `plugins/omniforge/`
- Marketplace name: `omnireview-marketplace` → `omniforge-marketplace`
- MCP server: `omnireview_mcp_server.py` → `omniforge_mcp_server.py`
- MCP tool prefix: `mcp__omnireview__*` → `mcp__omniforge__*`

### Migration
Existing users must uninstall and reinstall:
```bash
# Remove old installation
claude plugin uninstall omnireview
claude plugin marketplace remove omnireview-marketplace

# Install new
claude plugin marketplace add https://github.com/nexiouscaliver/OmniForge.git
claude plugin install omniforge@omniforge-marketplace
```

### Changed
- README fully rewritten to reflect OmniForge as MR lifecycle toolkit
- Plugin description updated to cover all 3 skills
- Keywords expanded for better discoverability
- Version bumped to 2.0.0 (semver major)

---

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
- OmniForge SKILL.md adds "Large Diff Strategy" section for context-efficient agent dispatch
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
- README updated with new "Included Skills" section documenting `/omnireview-gitlab`, `/omnifix-gitlab`, and `/omnicreate-gitlab`
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
- Marketplace distribution format (`marketplace.json` + `plugins/omniforge/` subdirectory)
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
