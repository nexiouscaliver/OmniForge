---
name: omnireview
description: Use when reviewing a GitLab merge request, performing code review on an MR, checking MR security, or when given a GitLab MR number or URL to review
argument-hint: <mr-number>
allowed-tools: [Read, Glob, Grep, Bash, Agent, Write, Edit]
---

# OmniReview

> **Multi-agent adversarial MR review — 3 parallel agents, 3 worktrees, 1 consolidated report.**

Dispatch 3 parallel specialized agents in isolated git worktrees to perform adversarial, independent analysis of a GitLab MR. Consolidate findings via confidence scoring, present actionable report, then offer structured actions (comment, create issues, approve).

**Core principle:** Independent adversarial review + confidence filtering + worktree isolation = high-signal feedback with minimal noise.

**Announce at start:** "I'm using OmniReview to review MR !{id}."

## Prerequisites

- `glab` CLI authenticated (`glab auth status` to verify)
- Git repository with remote pointing to GitLab
- Current working directory is in the git repo

## Input Parsing

Accept any of: MR number (`136`), prefixed (`!136`), or full GitLab URL.
Extract MR ID. If URL provided, extract project path and MR IID.

## The Process

```dot
digraph omnireview_flow {
    rankdir=TB;
    node [shape=box];

    gather [label="Phase 1: Gather MR Data\nglab mr view + diff + comments"];
    worktrees [label="Phase 2: Create 3 Worktrees\n(on MR source branch)"];
    dispatch [label="Phase 3: Dispatch 3 OmniReview Agents\nIN PARALLEL"];
    analyst [label="MR Analyst (OmniReview)\n(worktree 1)"];
    codebase [label="Codebase Reviewer (OmniReview)\n(worktree 2)"];
    security [label="Security Reviewer (OmniReview)\n(worktree 3)"];
    consolidate [label="Phase 4: Consolidation\nconfidence scoring + cross-correlation"];
    report [label="Phase 5: Present Report\n(NEVER auto-post)"];
    action [label="Phase 6: Action Menu\n(user chooses)"];
    cleanup [label="Phase 7: Cleanup\n(ALWAYS runs)"];

    gather -> worktrees -> dispatch;
    dispatch -> analyst;
    dispatch -> codebase;
    dispatch -> security;
    analyst -> consolidate;
    codebase -> consolidate;
    security -> consolidate;
    consolidate -> report -> action -> cleanup;
}
```

---

## Phase 1: Gather MR Data

Fetch ALL data before dispatching agents. Agents get data injected — they never re-fetch.

**If MCP tools are available** (plugin install), use the single tool call:

```
mcp__omnireview__fetch_mr_data(mr_id="{id}", repo_root="{cwd}")
```

Returns a structured JSON package with: mr_id, title, author, source_branch, target_branch, pipeline_status, description, comments, diff, diff_line_count, diff_too_large, diff_truncated, **diff_line_map**, commits, files_changed, labels, assignees, reviewers.

**IMPORTANT: `diff_line_map` is already included in this response.** It contains exact changed line numbers per file (added_lines, all_new_lines, hunks). When posting inline threads, use these line numbers directly — do NOT call `map_diff_lines` separately. The standalone `map_diff_lines` tool is only for re-parsing if you have a diff string from another source.

If `diff_too_large` is true, the diff is auto-truncated to 10,000 lines. Agents explore full files in their worktrees instead.

**Error handling:** If the tool returns `success: false`, check `error_type`:
- `auth_failure` — Tell user to run `glab auth login`
- `mr_not_found` — Verify MR number and repository
- `validation_error` — Check input format

**Fallback (personal skill install without MCP server):** Use bash commands directly:

```bash
glab auth status
glab mr view {id} -F json
glab mr view {id} -c
glab mr diff {id} --raw
git fetch origin {source_branch} {target_branch}
git log --oneline origin/{target_branch}..origin/{source_branch}
```

---

## Phase 2: Create 3 Worktrees

**REQUIRED SUB-SKILL:** Follow `superpowers:using-git-worktrees` pattern.

**If MCP tools are available** (plugin install), use the single tool call:

```
mcp__omnireview__create_review_worktrees(
    mr_id="{id}",
    source_branch="{from Phase 1 response}",
    repo_root="{cwd}"
)
```

Returns absolute paths for all 3 worktrees:
- `worktrees.analyst` — MR Analyst (OmniReview)
- `worktrees.codebase` — Codebase Reviewer (OmniReview)
- `worktrees.security` — Security Reviewer (OmniReview)

The tool automatically: ensures `.worktrees/` exists and is gitignored, cleans stale worktrees from crashed runs, fetches the source branch, creates 3 detached worktrees, resolves absolute paths.

**Error handling:** If creation fails partway, the tool auto-cleans any partially created worktrees and returns `cleanup_performed: true`.

**Fallback (personal skill install without MCP server):**

```bash
mkdir -p .worktrees
git check-ignore -q .worktrees 2>/dev/null || echo ".worktrees/" >> .gitignore
git worktree remove .worktrees/omni-analyst-{id} --force 2>/dev/null
git worktree remove .worktrees/omni-codebase-{id} --force 2>/dev/null
git worktree remove .worktrees/omni-security-{id} --force 2>/dev/null
git worktree prune
git fetch origin {source_branch}
git worktree add .worktrees/omni-analyst-{id} origin/{source_branch} --detach
git worktree add .worktrees/omni-codebase-{id} origin/{source_branch} --detach
git worktree add .worktrees/omni-security-{id} origin/{source_branch} --detach
ANALYST_PATH=$(cd .worktrees/omni-analyst-{id} && pwd)
CODEBASE_PATH=$(cd .worktrees/omni-codebase-{id} && pwd)
SECURITY_PATH=$(cd .worktrees/omni-security-{id} && pwd)
```

---

## Phase 3: Dispatch 3 OmniReview Agents in Parallel

**REQUIRED SUB-SKILL:** Use `superpowers:dispatching-parallel-agents` pattern.

Dispatch all 3 agents simultaneously using the **Agent tool** (NOT TaskCreate — the Agent tool spawns subagents). Send a single message with 3 parallel Agent tool calls. Each agent gets:
- Full MR context package (injected, NOT fetched by agent)
- Its own worktree **absolute** path for exploration
- Agent-specific review prompt (from template file)
- MR comments/discussions (all 3 agents, not just the MR Analyst)
- Confidence scoring instructions

### Agent 1: MR Analyst (OmniReview)
- **Template:** `./references/mr-analyst-prompt.md`
- **Worktree:** `.worktrees/omni-analyst-{id}`
- **Focus:** Process quality — commit-by-commit analysis, MR description, discussions, scope

### Agent 2: Codebase Reviewer (OmniReview)
- **Template:** `./references/codebase-reviewer-prompt.md`
- **Worktree:** `.worktrees/omni-codebase-{id}`
- **Focus:** Code quality — architecture, logic, testing, patterns, DRY, performance

### Agent 3: Security Reviewer (OmniReview)
- **Template:** `./references/security-reviewer-prompt.md`
- **Worktree:** `.worktrees/omni-security-{id}`
- **Focus:** Security — OWASP Top 10, secrets, auth/authz, injection, data exposure

**Model selection:** Use the most capable model available (opus) for all 3 agents. Review requires judgment, not just mechanical work.

### Agent Prompt Construction

For each agent, fill the template placeholders:
- `{MR_ID}` — MR number
- `{MR_TITLE}` — MR title from JSON
- `{WORKTREE_PATH}` — **Absolute** path to agent's worktree (convert from relative)
- `{MR_JSON_DATA}` — Full JSON metadata (all 3 agents get this)
- `{MR_DESCRIPTION}` — MR description text
- `{MR_COMMENTS}` — All discussion threads (all 3 agents get this — discussions may contain security/code context)
- `{MR_DIFF}` — Raw diff output
- `{COMMIT_LIST}` — Commit SHAs and messages
- `{FILES_CHANGED_LIST}` — List of changed file paths
- `{SOURCE_BRANCH}` — MR source branch name
- `{TARGET_BRANCH}` — MR target branch name

---

## Phase 4: Consolidation

Follow `./references/consolidation-guide.md` for the full algorithm.

### Confidence Scoring

Each agent assigns confidence (0-100) per finding:

| Score | Meaning | Threshold |
|-------|---------|-----------|
| 90-100 | Verified with code evidence | Include |
| 70-89 | Strong signal, likely real | Include |
| 50-69 | Possible, needs judgment | Filtered out |
| < 50 | Noise | Filtered out |

**Threshold: 70.** Only findings >= 70 appear in the final report.

### Cross-Correlation

- Same area flagged by 2 agents: **+15 confidence** (cap 100)
- Same area flagged by 3 agents: **+25 confidence** (cap 100)
- Contradictory findings: Present both, mark "needs human judgment"

### False Positive Auto-Reduction (-30 confidence)

- Pre-existing issues (code predates this MR per `git blame`)
- Linter/CI-catchable issues
- Pure style nitpicks without functional impact
- Issues already resolved in MR discussions

### Deduplication

- Group by file:line proximity (within 5 lines = same area)
- Merge overlapping findings, keep highest severity
- Preserve unique insights from each agent

---

## Phase 5: Present Report

```markdown
## OmniReview Report: !{id} — {title}

**Branch:** {source} → {target} | **Author:** {author} | **Pipeline:** {status}

### Summary
[1-3 sentence executive summary: overall assessment, key risks]

### Verdict: [APPROVE | APPROVE_WITH_FIXES | REQUEST_CHANGES | BLOCK]

### Strengths
[Specific things done well with file:line references]

### Issues

#### Critical (Must Fix Before Merge)
[Bugs, security vulnerabilities, data loss risks]
Each: file:line | description | why it matters | how to fix | confidence | source agent(s)

#### Important (Should Fix)
[Architecture problems, missing edge cases, test gaps]

#### Minor (Nice to Have)
[Optimizations, documentation, code clarity]

### MR Process Notes
[From MR Analyst (OmniReview): commit quality, description, unresolved discussions]

### Security Assessment
[From Security Reviewer (OmniReview): OWASP findings, posture assessment]

### Recommendations
[Future improvements beyond this MR's scope]

### Agent Agreement Matrix
| File/Area | MR Analyst (OmniReview) | Codebase (OmniReview) | Security (OmniReview) | Consensus |
```

**CRITICAL: Present the report FIRST. Never auto-post anything.**

---

## Phase 6: Action Menu

After presenting the full report, offer structured actions:

```
What would you like to do?

1. Full review post — summary comment + inline discussion threads (Recommended)
2. Post summary only (overview comment, no inline threads)
3. Post inline findings only (discussion threads, no summary)
4. Create GitLab issues for Critical/Important items
5. Approve the MR
6. Open MR in browser
7. Re-review a specific area (single focused agent)
8. Verify a specific concern (run a targeted check)
9. Done — no action needed
```

Wait for user choice. Execute chosen action(s). Return to menu until user selects "Done".

### Option 1: Full Review Post (Recommended)

This is the most common action. It posts everything in one go:

1. Post the **summary comment** (overview template below) as a top-level MR note.
2. For **EACH finding** with confidence >= 70, post a separate **inline discussion thread** on the relevant diff line.
3. Report back: "Posted summary comment + {N} inline discussion threads"

**Do NOT batch findings.** Each finding gets its own thread so the MR author can resolve them independently. Multiple threads on the same file = expected and encouraged.

**Implementation:** Use the `mcp__omnireview__post_full_review` tool which handles the summary and all inline threads efficiently in a single tool call.

**Line numbers:** Use the `diff_line_map` from the `fetch_mr_data` response (Phase 1) to get valid line numbers for each file. The `added_lines` array contains exact line numbers where code was added — use these for `line_number` in findings. Do NOT call `map_diff_lines` separately — the data is already available from Phase 1.

### Summary Comment Template

The summary is an **overview** — a high-level snapshot for anyone (author, reviewer, PM) to quickly understand the MR state. Keep it concise. The detail lives in the inline threads.

```markdown
## OmniReview

**Verdict:** {APPROVE | APPROVE_WITH_FIXES | REQUEST_CHANGES | BLOCK}

### Overview

{2-4 sentences: what this MR does, the overall quality assessment, and the key risk areas if any}

### At a Glance

| | Count |
|---|---|
| Critical | {N} |
| Important | {N} |
| Minor | {N} |

### Strengths
{Top 2-3 things done well — brief, specific}

### Key Concerns
{Top 2-3 most important findings — one line each, referencing the inline threads for detail}

### Security
{1-2 sentences: security posture assessment. "No security concerns found." or "See inline threads for {N} security findings."}

---
*Reviewed by 3 parallel agents (MR Analyst, Codebase Reviewer, Security Reviewer)*
*Confidence threshold: 70/100 | {N} findings above threshold*
```

### Inline Discussion Thread Template

Each thread is **technical and actionable**. One thread per finding. Don't hold back — post as many threads as there are findings.

```markdown
**{SEVERITY}** — {short_title}

**What:** {1-2 sentence description of the issue}

**Why it matters:** {impact — what could go wrong}

**Recommendation:**
\`\`\`{language}
{code suggestion or description of fix}
\`\`\`

Confidence: {score}/100 | Found by: {agent_name(s)}
```

**For security findings, also include:**
```markdown
**Attack scenario:** {how this could be exploited}
```

**Rules for inline threads:**
- Post each finding as a **separate** discussion thread on the relevant diff line
- If a finding spans multiple lines, place it on the most relevant line
- Multiple threads on the same file = expected and encouraged
- Don't merge or batch findings — each gets its own thread for independent resolution
- Severity tag at the start: **Critical**, **Important**, or **Minor**
- Include code suggestions in fenced blocks where applicable

### Action Commands

**If MCP tools are available** (recommended — handles all GitLab API complexity automatically):

| Action | MCP Tool |
|--------|----------|
| Full review post | `mcp__omnireview__post_full_review(mr_id, summary, findings_json, repo_root)` |
| Summary only | `mcp__omnireview__post_review_summary(mr_id, summary, repo_root)` |
| Inline thread | `mcp__omnireview__post_inline_thread(mr_id, file_path, line_number, body, repo_root)` |
| Create issue | `mcp__omnireview__create_linked_issue(mr_id, title, description, labels, repo_root)` |

For `post_full_review`, the `findings` parameter is a JSON string of an array:
```json
[
  {"file_path": "src/app.py", "line_number": 42, "body": "**Important** — Missing null check\n\n**What:** ..."},
  {"file_path": "src/app.py", "line_number": 87, "body": "**Minor** — Magic number\n\n**What:** ..."}
]
```

The MCP tools automatically fetch diff position SHAs, URL-encode the project path, and construct the GitLab API request. The model only needs to provide the text and line numbers.

**Fallback (no MCP server):**

| Action | Command |
|--------|---------|
| Summary | `glab mr note {id} -m "{summary}"` |
| Inline thread | `glab api projects/:fullpath/merge_requests/{iid}/discussions --method POST --raw-field "body={text}" --raw-field "position[position_type]=text" --raw-field "position[base_sha]={sha}" --raw-field "position[head_sha]={sha}" --raw-field "position[start_sha]={sha}" --raw-field "position[new_path]={file}" --raw-field "position[new_line]={line}"` |
| Create issue | `glab issue create -t "[MR !{id}] {title}" -d "{desc}" --linked-mr {id} --no-editor` |
| Approve | `glab mr approve {id}` |
| Open browser | `glab mr view {id} -w` |

**No AI attribution in any posted content.** Write as a standard code review comment.

---

## Phase 7: Cleanup

**ALWAYS runs, regardless of success or failure.**

**If MCP tools are available:**

```
mcp__omnireview__cleanup_review_worktrees(mr_id="{id}", repo_root="{cwd}")
```

The tool force-removes all 3 worktrees, cleans leftover directories, and prunes git worktree references. Reports what was removed vs. already clean.

**Fallback (personal skill install without MCP server):**

```bash
git worktree remove .worktrees/omni-analyst-{id} --force 2>/dev/null
git worktree remove .worktrees/omni-codebase-{id} --force 2>/dev/null
git worktree remove .worktrees/omni-security-{id} --force 2>/dev/null
rm -rf .worktrees/omni-analyst-{id} .worktrees/omni-codebase-{id} .worktrees/omni-security-{id} 2>/dev/null
git worktree prune
```

---

## Error Handling

| Error | Response |
|-------|----------|
| glab not authenticated | "Run `glab auth login` first." Stop. |
| MR not found | "MR !{id} not found. Verify the number and repository." Stop. |
| Network failure | Retry glab command once. If still fails, report error and stop. |
| Worktree creation fails | Try with timestamp suffix. If still fails, clean up and stop. |
| 1 agent fails | Continue with 2/3. Note gap in report. |
| 2+ agents fail | Present partial results. Suggest manual review. |
| Diff too large | Summarize diff. Give agents file list to explore in worktrees. |
| Cleanup fails | Force remove directories. Report if still stuck. |

**Cleanup guarantee:** The entire flow is wrapped in a try/finally pattern. Phase 7 runs no matter what.

---

## Quick Reference

| Phase | What | How |
|-------|------|-----|
| 1. Gather | Fetch MR data | `glab mr view/diff` (JSON + raw) |
| 2. Setup | Create 3 worktrees | `git worktree add --detach` (×3) |
| 3. Review | Dispatch OmniReview agents | Agent tool parallel (×3, opus model) |
| 4. Merge | Consolidate findings | Confidence score + cross-correlate + dedup |
| 5. Report | Present OmniReview report | Structured markdown with verdict |
| 6. Act | User chooses | `glab mr note/approve`, `glab issue create` |
| 7. Clean | Remove worktrees | `git worktree remove --force` (×3) + prune |

---

## The "Small MR" Trap

```
EVERY MR gets the full OmniReview process. No exceptions.
```

The most dangerous rationalization is: "This MR is small/simple, I'll just do a quick review." This is exactly when issues get missed — when you assume simplicity means safety.

**Baseline testing proved this.** Without OmniReview, an agent reviewing a "simple" 86-line CI/CD change:
- Skipped worktree isolation ("unnecessary overhead")
- Skipped parallel agents ("scope was small enough")
- Skipped confidence scoring ("informal severity labels are fine")
- Read the wrong branch (local instead of MR source)
- Did no systematic security check (just "no issues found")

A CI/CD file change can expose secrets, break production deployments, or modify security configurations. "Small" does not mean "safe."

**The rule:** Every MR gets 3 OmniReview agents, 3 worktrees, confidence scoring, and the full report. The overhead is minutes. The cost of a missed issue is hours or days.

---

## Red Flags — STOP and Follow the Process

| Thought | Reality |
|---------|---------|
| "This MR is too small for full review" | Small MRs in CI/CD, auth, or config are the most dangerous. Full process. |
| "I'll just read the diff, no worktree needed" | The diff is insufficient. You need full file context. Create worktrees. |
| "One agent can handle this" | You'll miss cross-perspective insights. Dispatch all 3. |
| "I'll skip confidence scoring, findings are obvious" | "Obvious" findings are often false positives. Score everything. |
| "Let me just post my comments now" | Present the report first. User decides what to post. |
| "I can use the local branch instead of MR source" | The local branch may be stale or different. Always fetch and use MR source. |
| "Security review isn't needed for this change" | Every change has security implications. Always run the security agent. |
| "Cleanup can wait" | Stale worktrees accumulate. Clean up immediately. |
| "The MR comments aren't relevant for code/security review" | Discussions often contain security context, design decisions, and known risks. Inject comments into ALL agents. |
| "Relative paths are fine for worktrees" | Agents need absolute paths to reliably navigate. Always convert to absolute. |
| "I'll use TaskCreate to track the agents" | TaskCreate tracks YOUR work. Use the Agent tool to dispatch subagents. Different tools, different purposes. |

**All of these mean: Follow the 7-phase OmniReview process. No shortcuts.**

---

## Never

- Use `gh` (this is GitLab — use `glab` exclusively)
- Push to origin (user's rule: never push without explicit permission)
- Post comments without user approval from the action menu
- Approve MR without explicit user request
- Skip worktree cleanup (even on failure)
- Include findings below confidence threshold 70
- Let agents share worktrees (isolation is critical)
- Add AI attribution to posted comments (no "Generated by Claude" etc.)
- Skip any of the 7 phases for any reason

## Always

- Fetch MR data in Phase 1 before dispatching agents
- Create isolated worktrees per agent on the MR source branch
- Inject context into agent prompts (don't make agents re-fetch)
- Use confidence scoring with threshold 70
- Cross-correlate findings across agents
- Present full OmniReview report before any action
- Ask user which actions to take via the action menu
- Clean up all worktrees regardless of outcome
- Use `glab` for all GitLab operations

---

## Integration

**Uses:**
- `superpowers:using-git-worktrees` — Worktree setup/teardown pattern
- `superpowers:dispatching-parallel-agents` — Parallel dispatch pattern
- `superpowers:requesting-code-review` — Review output format (Strengths/Issues/Assessment)
- `superpowers:verification-before-completion` — Evidence-based findings

**OmniReview Agent Templates:**
- `./references/mr-analyst-prompt.md` — MR Analyst (OmniReview)
- `./references/codebase-reviewer-prompt.md` — Codebase Reviewer (OmniReview)
- `./references/security-reviewer-prompt.md` — Security Reviewer (OmniReview)
- `./references/consolidation-guide.md` — Cross-correlation and report format
