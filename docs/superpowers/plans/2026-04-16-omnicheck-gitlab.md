# OmniCheck GitLab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a standalone `omnicheck-gitlab` skill that checks whether requested MR review findings have been applied, and posts nudge comments on unaddressed threads.

**Architecture:** New skill directory under `plugins/omniforge/skills/omnicheck-gitlab/` with three files: the main SKILL.md orchestration file, an analysis subagent prompt, and a nudge format reference. No new MCP tools required — uses four existing tools (`fetch_mr_discussions`, `fetch_mr_data`, `reply_to_discussion`, `post_review_summary`). No worktrees — purely diff-based analysis.

**Tech Stack:** Markdown skill files only. No Python code changes needed. Uses existing MCP server.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `plugins/omniforge/skills/omnicheck-gitlab/SKILL.md` | Create | Main 5-phase orchestration |
| `plugins/omniforge/skills/omnicheck-gitlab/references/analysis-agent-prompt.md` | Create | Subagent prompt: threads + diff → verdicts |
| `plugins/omniforge/skills/omnicheck-gitlab/references/nudge-guide.md` | Create | Exact format for report, thread replies, and summary comment |
| `plugins/omniforge/.claude-plugin/plugin.json` | Modify | Add omnicheck to description and keywords |
| `CLAUDE.md` | Modify | Document the new skill in repository layout |

---

## Task 1: Create skill directory and main SKILL.md

**Files:**
- Create: `plugins/omniforge/skills/omnicheck-gitlab/SKILL.md`

- [ ] **Step 1: Create the skill directory**

```bash
mkdir -p plugins/omniforge/skills/omnicheck-gitlab/references
```

Expected: no output, directory exists.

- [ ] **Step 2: Create SKILL.md**

Write `plugins/omniforge/skills/omnicheck-gitlab/SKILL.md` with this exact content:

```markdown
---
name: omnicheck-gitlab
description: Use when checking if MR review findings have been applied — verifies both OmniForge-generated and human reviewer comments against the current diff, posts nudge replies on unaddressed threads
argument-hint: <mr-number>
allowed-tools: [Read, Glob, Grep, Bash, Agent, Write, Edit]
---

# OmniCheck

> **Verify whether requested MR changes have been applied — diff analysis + targeted nudge comments.**

Check all discussion threads on a GitLab MR against the current diff. Resolved threads are marked APPLIED. Unresolved threads are analyzed by a subagent to determine if the fix was applied silently or not at all. Unaddressed threads receive nudge replies and a summary comment.

**Core principle:** Thread status check + diff analysis + user approval gate = accurate, non-spammy follow-up.

**Announce at start:** "I'm using OmniCheck to verify findings on MR !{id}."

## Prerequisites

- `glab` CLI authenticated (`glab auth status` to verify)
- Git repository with remote pointing to GitLab
- Current working directory is in the git repo
- MR must have discussion threads

## Input Parsing

Accept any of: MR number (`136`), prefixed (`!136`), or full GitLab URL.
Extract MR ID. If URL provided, extract project path and MR IID.

## The Process

```
Phase 1: GATHER   — fetch all threads + MR diff/data
Phase 2: ANALYZE  — single subagent checks each open thread against the diff
Phase 3: REPORT   — present status table, user approves nudge list
Phase 4: NUDGE    — reply on each NOT_APPLIED thread + post summary MR comment
Phase 5: DONE     — no cleanup needed (no worktrees)
```

---

## Thread Verdicts

| Verdict | Meaning | Action |
|---------|---------|--------|
| `APPLIED` | Thread is resolved — trusted as-is | None |
| `SILENTLY_APPLIED` | Thread open, but diff shows the concern was addressed | Note in summary only |
| `NOT_APPLIED` | Thread open, diff shows no relevant change | Nudge: thread reply + summary row |
| `NEEDS_HUMAN` | Ambiguous — cannot determine from diff alone | Flag in report; no automatic nudge |

---

## Phase 1: Gather

**Step 1:** Fetch all discussion threads.

```
mcp__omniforge__fetch_mr_discussions(mr_id="{id}", repo_root="{cwd}")
```

Returns structured threads with: `discussion_id`, `resolvable`, `resolved`, `type`, `file_path`, `line_number`, `body`, `author`, `replies`.

**Step 2:** Partition threads.
- `resolved: true` → pre-labeled `APPLIED`, skip analysis
- `resolvable: true` AND `resolved: false` → pass to Phase 2
- `resolvable: false` (system notes, pipeline status) → skip entirely

**Step 3:** Fetch MR metadata and diff.

```
mcp__omniforge__fetch_mr_data(mr_id="{id}", repo_root="{cwd}")
```

Returns: title, author, source_branch, target_branch, diff, diff_line_count, commits, files_changed.

**Step 4:** Early exit checks.
- Zero resolvable threads: "MR !{id} has no discussion threads. Nothing to check." Stop.
- Zero unresolved threads: "All {N} threads are resolved. Nothing to nudge." Stop.

**Step 5:** Present: "Found {N} total threads ({R} resolved, {U} unresolved). Analyzing {U} unresolved threads."

---

## Phase 2: Analyze (Single Subagent)

**Goal:** For each unresolved thread, determine if the diff addresses its concern.

**Template:** `./references/analysis-agent-prompt.md`

Fill template placeholders:
- `{MR_ID}` — MR number
- `{MR_TITLE}` — MR title
- `{UNRESOLVED_THREADS_JSON}` — JSON array of all unresolved threads
- `{GIT_DIFF}` — Full diff string from Phase 1

**Large MR handling:** If unresolved thread count > 15 AND diff_line_count > 5000, group threads by file and dispatch up to 3 subagents. Merge results before Phase 3.

### Expected Return

```json
[
  {
    "discussion_id": "abc123",
    "file_path": "src/auth.py",
    "line_number": 47,
    "body_summary": "Missing null check on user input",
    "verdict": "NOT_APPLIED",
    "confidence": 91,
    "reasoning": "The diff shows no changes to src/auth.py around line 47. The null check is still absent."
  },
  {
    "discussion_id": "def456",
    "file_path": ".gitlab-ci.yml",
    "line_number": 1072,
    "body_summary": "Missing placeholder mapping for STRIPE_PRICE_ENTERPRISE",
    "verdict": "SILENTLY_APPLIED",
    "confidence": 87,
    "reasoning": "Line 1072 in .gitlab-ci.yml was changed in the diff to include the placeholder mapping. The thread was not resolved but the concern is addressed."
  }
]
```

**Verdict definitions:**
- `SILENTLY_APPLIED` — thread open but diff shows the concern was addressed
- `NOT_APPLIED` — thread open and diff shows no relevant change
- `NEEDS_HUMAN` — diff changes are present but genuinely unclear if they address the concern

---

## Phase 3: Report (User Approval Gate)

**REQUIRED REFERENCE:** `./references/nudge-guide.md` — read before presenting results. Contains the exact presentation format and user action matrix. Do NOT present without loading this reference.

Present status combining Phase 1 resolved threads + Phase 2 verdicts:

```
OmniCheck — MR !{id}: {title}

  ✓ Applied (resolved):     {N} threads
  ✓ Silently Applied:       {N} threads
  ✗ Not Applied:            {N} threads
  ? Needs Human Review:     {N} threads

NOT_APPLIED threads (will receive nudge):
  1. {file}:{line} — {body_summary} [confidence: {score}%]
  2. {file}:{line} — {body_summary} [confidence: {score}%]

NEEDS_HUMAN threads (no automatic nudge):
  3. general — {body_summary}

Post nudge replies on NOT_APPLIED threads? [Y/n]
(Enter numbers to exclude specific threads, e.g. "exclude 2")
```

**CRITICAL: No comments are posted until the user explicitly approves.**

---

## Phase 4: Nudge

**REQUIRED REFERENCE:** `./references/nudge-guide.md` — contains the exact thread reply template and summary comment template. Do NOT post without loading this reference.

For each approved NOT_APPLIED thread:

**Step 1:** Post thread reply.
```
mcp__omniforge__reply_to_discussion(
  mr_id="{id}",
  discussion_id="{discussion_id}",
  body="{nudge_reply_text}",
  repo_root="{cwd}"
)
```

**Step 2:** After all thread replies succeed or fail, post one summary comment.
```
mcp__omniforge__post_review_summary(
  mr_id="{id}",
  summary="{summary_comment_text}",
  repo_root="{cwd}"
)
```

**Ordering guarantee:** All thread replies before the summary comment.

**On reply failure:** Collect the failure, continue with remaining threads, and note the failure in the summary comment.

---

## Phase 5: Done

No worktrees → no cleanup needed.

Report:
```
OmniCheck complete — MR !{id}

  ✓ Nudged: {N} threads
  ✗ Failed to post: {N} threads (list them)
```

---

## Error Handling

| Error | Response |
|-------|----------|
| glab not authenticated | "Run `glab auth login` first." Stop. |
| MR not found | "MR !{id} not found. Verify the number and repository." Stop. |
| No discussion threads | "MR !{id} has no discussion threads. Nothing to check." Stop. |
| All threads resolved | "All {N} threads are resolved. Nothing to nudge." Stop. |
| Analysis agent fails | Present error to user; offer to retry or abort |
| Thread reply fails | Continue with remaining threads; note failure in summary |
| Summary comment fails | Report failure; thread replies already posted |

---

## Integration

**MCP Tools:**
- `mcp__omniforge__fetch_mr_discussions` — Fetch all discussion threads
- `mcp__omniforge__fetch_mr_data` — Fetch MR metadata and diff
- `mcp__omniforge__reply_to_discussion` — Post nudge reply on a thread
- `mcp__omniforge__post_review_summary` — Post summary comment on MR

**Subagent Template:**
- `./references/analysis-agent-prompt.md` — Analysis Agent (single, read-only diff check)

---

## Never

- Post comments without explicit user approval (Phase 3 gate)
- Add AI attribution to any posted comment
- Use `gh` (GitLab — use `glab` exclusively)
- Auto-resolve any threads (OmniCheck only nudges, never resolves)
- Create worktrees (diff-only analysis)
- Skip any of the 5 phases

## Always

- Fetch threads and diff in Phase 1 before dispatching analysis
- Present status table and wait for user approval before posting
- Post thread replies before the summary comment
- Report final outcome including any failed posts
- Use `glab` for all GitLab operations
```

- [ ] **Step 3: Verify file exists and has frontmatter**

```bash
head -6 plugins/omniforge/skills/omnicheck-gitlab/SKILL.md
```

Expected output:
```
---
name: omnicheck-gitlab
description: Use when checking if MR review findings...
argument-hint: <mr-number>
allowed-tools: [Read, Glob, Grep, Bash, Agent, Write, Edit]
---
```

- [ ] **Step 4: Commit**

```bash
git add plugins/omniforge/skills/omnicheck-gitlab/SKILL.md
git commit -m "feat: add omnicheck-gitlab skill — 5-phase MR finding verification"
```

---

## Task 2: Create analysis-agent-prompt.md

**Files:**
- Create: `plugins/omniforge/skills/omnicheck-gitlab/references/analysis-agent-prompt.md`

- [ ] **Step 1: Create the analysis agent prompt**

Write `plugins/omniforge/skills/omnicheck-gitlab/references/analysis-agent-prompt.md` with this exact content:

```markdown
# Analysis Agent (OmniCheck)

You are the **Analysis Agent** for OmniCheck — determining whether unresolved discussion threads on MR !{MR_ID}: **{MR_TITLE}** have been addressed in the current code diff.

---

## Unresolved Threads

{UNRESOLVED_THREADS_JSON}

---

## Current Diff

{GIT_DIFF}

---

## Your Job

For each thread, determine whether the code changes in the diff address the thread's concern.

### Step 1: Understand the concern

Read the thread body carefully:
- What specifically was the reviewer asking for?
- What file and line does it refer to?
- Is the concern about a code change, missing logic, style, security, or something else?

### Step 2: Check the diff

- Does the diff contain changes to the file mentioned in the thread?
- Do those changes directly address the specific concern raised?
- Could the concern be addressed in a different file from what the thread mentions?

### Step 3: Assign a verdict

- **`SILENTLY_APPLIED`** — The diff contains changes that clearly address the thread's concern, even though the thread was not resolved by the reviewer.
- **`NOT_APPLIED`** — The diff shows no changes relevant to the thread's concern. The concern remains outstanding.
- **`NEEDS_HUMAN`** — The diff has changes in the relevant area, but it is genuinely unclear whether they address the concern. Flag it for human judgment.

### Confidence

Score your confidence from 50–100:
- 90–100: The answer is unambiguous from the diff
- 70–89: Reasonably confident but some ambiguity
- 50–69: Best guess; a human should verify

---

## Output Format

Return a JSON array — one object per thread. Every thread from the input MUST appear in the output.

```json
[
  {
    "discussion_id": "abc123",
    "file_path": "src/auth.py",
    "line_number": 47,
    "body_summary": "1-sentence summary of what the thread asked for",
    "verdict": "NOT_APPLIED",
    "confidence": 91,
    "reasoning": "The diff shows no changes to src/auth.py around line 47. The null check is still absent."
  },
  {
    "discussion_id": "def456",
    "file_path": ".gitlab-ci.yml",
    "line_number": 1072,
    "body_summary": "Missing placeholder mapping for STRIPE_PRICE_ENTERPRISE",
    "verdict": "SILENTLY_APPLIED",
    "confidence": 87,
    "reasoning": "Line 1072 in .gitlab-ci.yml was changed in the diff to include the placeholder mapping. The concern is addressed even though the thread was not resolved."
  }
]
```

**Field rules:**
- `discussion_id` — copy exactly from the input thread
- `file_path` — the file the thread concerns (use `general` if no file applies)
- `line_number` — the line number from the thread, or `null` if no line applies
- `body_summary` — one sentence describing what the reviewer asked for
- `verdict` — one of: `SILENTLY_APPLIED`, `NOT_APPLIED`, `NEEDS_HUMAN`
- `confidence` — integer 50–100
- `reasoning` — 1–3 sentences grounded in the diff

---

## Rules

- **Every input thread must appear in the output.** Missing threads will be treated as `NEEDS_HUMAN`.
- **Stay grounded in the diff.** Do not speculate about code not shown. If you cannot see evidence of a fix, use `NOT_APPLIED` or `NEEDS_HUMAN`.
- **Be specific in reasoning.** Name the file and line. Quote the relevant diff line if helpful.
- **Do not suggest fixes.** You are an analyst. Return verdicts only.
- **body_summary must be one sentence.** It is displayed to the user and included in nudge comments.
- **Do not add commentary outside the JSON array.** Return the JSON array only.
```

- [ ] **Step 2: Verify file exists**

```bash
head -5 plugins/omniforge/skills/omnicheck-gitlab/references/analysis-agent-prompt.md
```

Expected:
```
# Analysis Agent (OmniCheck)

You are the **Analysis Agent** for OmniCheck — determining whether unresolved discussion threads on MR !{MR_ID}: **{MR_TITLE}** have been addressed in the current code diff.
```

- [ ] **Step 3: Commit**

```bash
git add plugins/omniforge/skills/omnicheck-gitlab/references/analysis-agent-prompt.md
git commit -m "feat: add analysis-agent-prompt for omnicheck-gitlab"
```

---

## Task 3: Create nudge-guide.md

**Files:**
- Create: `plugins/omniforge/skills/omnicheck-gitlab/references/nudge-guide.md`

- [ ] **Step 1: Create the nudge guide**

Write `plugins/omniforge/skills/omnicheck-gitlab/references/nudge-guide.md` with this exact content:

```markdown
# OmniCheck Nudge Guide

Reference for Phase 3 (reporting format) and Phase 4 (nudge posting format).

---

## Phase 3: Report Presentation Format

```
OmniCheck — MR !{id}: {title}

  ✓ Applied (resolved):     {N} threads
  ✓ Silently Applied:       {N} threads
  ✗ Not Applied:            {N} threads
  ? Needs Human Review:     {N} threads

NOT_APPLIED threads (will receive nudge):
  1. {file}:{line} — {body_summary} [confidence: {score}%]
  2. {file}:{line} — {body_summary} [confidence: {score}%]

NEEDS_HUMAN threads (no automatic nudge):
  3. general — {body_summary}

Post nudge replies on NOT_APPLIED threads? [Y/n]
(Enter numbers to exclude specific threads, e.g. "exclude 2")
```

**Notes:**
- For general (non-inline) threads, use `general` in place of `{file}:{line}`
- Order NOT_APPLIED threads by confidence descending (highest first)
- If there are zero NOT_APPLIED threads, skip the nudge prompt entirely and just report the status counts

### User Action Matrix

| Input | Meaning |
|-------|---------|
| `Y` or Enter | Nudge all NOT_APPLIED threads as listed |
| `n` | Cancel — post nothing |
| `exclude 2` | Remove thread #2 from nudge list, nudge the rest |
| `exclude 1,3` | Remove threads #1 and #3, nudge the rest |

---

## Phase 4: Thread Reply Template

Post this on each NOT_APPLIED thread via `reply_to_discussion`. Replace `{reasoning_condensed}` with 1–2 sentences from the analysis agent's reasoning — state what was expected and what's missing:

```
This concern appears unaddressed in the latest changes.

{reasoning_condensed}

Could you take another look? Marking for follow-up.
```

**Rules:**
- Maximum 4 sentences total (including the fixed opening and closing lines)
- Do not mention AI, automation, bots, OmniCheck, or OmniForge
- Do not use accusatory or demanding language
- Tone: factual and neutral

**Example:**
```
This concern appears unaddressed in the latest changes.

The null check on user input at `src/auth.py:47` does not appear in the diff — the variable is still passed directly to the database query without validation.

Could you take another look? Marking for follow-up.
```

---

## Phase 4: Summary Comment Template

Post one summary comment after all thread replies, via `post_review_summary`:

```
## Follow-up Check — MR !{id}

| Status | Count |
|--------|-------|
| ✓ Applied | {N} |
| ✓ Silently Applied | {N} |
| ✗ Not Applied | {N} |
| ? Needs Human | {N} |

### Unaddressed Findings

| Thread | File | Summary |
|--------|------|---------|
| #{discussion_id_short} | {file_path}:{line} | {body_summary} |
```

**Field rules:**
- `{discussion_id_short}` — first 7 characters of the discussion ID
- For general (non-inline) threads, use `general` in the File column
- Include only NOT_APPLIED threads in the "Unaddressed Findings" table — not NEEDS_HUMAN
- If zero NOT_APPLIED threads (all were approved by user exclusions), omit the table and write: "All flagged findings have been addressed."
- Do not mention AI, automation, bots, OmniCheck, or OmniForge in this comment
```

- [ ] **Step 2: Verify file exists**

```bash
head -5 plugins/omniforge/skills/omnicheck-gitlab/references/nudge-guide.md
```

Expected:
```
# OmniCheck Nudge Guide

Reference for Phase 3 (reporting format) and Phase 4 (nudge posting format).
```

- [ ] **Step 3: Verify all three skill files exist**

```bash
find plugins/omniforge/skills/omnicheck-gitlab -type f | sort
```

Expected:
```
plugins/omniforge/skills/omnicheck-gitlab/SKILL.md
plugins/omniforge/skills/omnicheck-gitlab/references/analysis-agent-prompt.md
plugins/omniforge/skills/omnicheck-gitlab/references/nudge-guide.md
```

- [ ] **Step 4: Commit**

```bash
git add plugins/omniforge/skills/omnicheck-gitlab/references/nudge-guide.md
git commit -m "feat: add nudge-guide for omnicheck-gitlab reporting and posting format"
```

---

## Task 4: Update plugin.json and CLAUDE.md

**Files:**
- Modify: `plugins/omniforge/.claude-plugin/plugin.json`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update plugin.json description and keywords**

In `plugins/omniforge/.claude-plugin/plugin.json`, change:

```json
"description": "AI-powered merge request toolkit — review MRs with 3 parallel adversarial agents, auto-fix review findings, and create MRs from commits. 13 MCP tools, git worktree isolation, confidence-scored findings."
```

to:

```json
"description": "AI-powered merge request toolkit — review MRs with 3 parallel adversarial agents, auto-fix review findings, verify applied changes, and create MRs from commits. 13 MCP tools, git worktree isolation, confidence-scored findings."
```

And add `"omnicheck"` to the keywords array:

```json
"keywords": [
  "merge-request",
  "gitlab",
  "code-review",
  "security",
  "multi-agent",
  "auto-fix",
  "omnicheck",
  "mr-creation",
  "worktree",
  "owasp",
  "mcp"
]
```

- [ ] **Step 2: Update CLAUDE.md skills list**

In `CLAUDE.md`, find the skills list under "What This Is" and add the omnicheck skill:

```markdown
## What This Is

OmniForge is a Claude Code plugin distributed as its own marketplace. It contains four skills:
- **omnireview-gitlab** — dispatches 3 parallel AI review agents in isolated git worktrees to adversarially review GitLab MRs
- **omnifix-gitlab** — automates fixing review findings with parallel triage subagents, sequential fixing, verification, and thread resolution
- **omnicreate-gitlab** — automates GitLab MR creation via `glab` CLI with auto-populated title/description from commits
- **omnicheck-gitlab** — checks whether requested MR changes have been applied by analyzing the diff against all discussion threads, posts nudge replies on unaddressed findings
```

Also update the repository layout section to include the new skill directory:

```
    skills/
      omnireview-gitlab/                ← Review skill (7-phase review workflow)
        SKILL.md
        references/                     ← 5 files: 3 agent prompts + consolidation guide + posting guide
      omnifix-gitlab/                   ← Fix skill (7-phase fix workflow)
        SKILL.md
        references/                     ← 5 files: 3 agent prompts + approval guide + commit/post guide
      omnicreate-gitlab/                 ← MR creation skill
        SKILL.md
      omnicheck-gitlab/                  ← Check skill (5-phase diff verification workflow)
        SKILL.md
        references/                     ← 2 files: analysis agent prompt + nudge guide
```

- [ ] **Step 3: Verify CLAUDE.md mentions omnicheck-gitlab**

```bash
grep -n "omnicheck" CLAUDE.md
```

Expected: at least 2 lines — one in the skills list, one in the layout.

- [ ] **Step 4: Commit**

```bash
git add plugins/omniforge/.claude-plugin/plugin.json CLAUDE.md
git commit -m "docs: register omnicheck-gitlab in plugin.json and CLAUDE.md"
```

---

## Self-Review Against Spec

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| New standalone skill `omnicheck-gitlab` | Task 1 |
| Phase 1: Gather threads + MR data | Task 1 (SKILL.md Phase 1) |
| Phase 2: Single analysis subagent | Task 1 (SKILL.md Phase 2) + Task 2 |
| Resolved threads = APPLIED, no analysis | Task 1 (SKILL.md Phase 1) |
| Four verdicts: APPLIED/SILENTLY_APPLIED/NOT_APPLIED/NEEDS_HUMAN | Task 1 + Task 2 |
| Phase 3: User approval gate before posting | Task 1 (SKILL.md Phase 3) + Task 3 |
| Phase 4: Thread reply on NOT_APPLIED | Task 1 (SKILL.md Phase 4) + Task 3 |
| Phase 4: Summary MR comment after thread replies | Task 1 (SKILL.md Phase 4) + Task 3 |
| No AI attribution in posted comments | Task 3 (nudge-guide.md rules) |
| Large MR handling (>15 threads, >5000 diff lines) | Task 1 (SKILL.md Phase 2) |
| User can exclude specific threads from nudge list | Task 3 (nudge-guide.md user action matrix) |
| Error handling table | Task 1 (SKILL.md error handling) |
| No new MCP tools | All tasks — confirmed no new tools |
| Update CLAUDE.md and plugin.json | Task 4 |

All spec requirements covered.
