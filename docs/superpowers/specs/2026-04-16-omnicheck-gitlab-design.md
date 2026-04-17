# OmniCheck GitLab — Design Spec

**Date:** 2026-04-16  
**Feature:** `omnicheck-gitlab` — standalone skill to verify whether requested MR changes have been applied  
**Status:** Approved for implementation

---

## Problem

After an MR review (via OmniReview or human reviewers), there is no automated way to check whether the author has actually addressed the findings. Reviewers must manually re-read the diff and cross-reference threads. This is slow and error-prone.

## Goal

A standalone skill that takes an MR number, checks all discussion threads against the current code diff, and posts nudge comments on threads where the requested change has not been applied.

---

## Scope

- New standalone skill: `omnicheck-gitlab`
- Invoked as: `/omnicheck-gitlab <mr-number>`
- Checks **all** discussion threads — OmniForge-generated findings and human reviewer comments alike
- No worktrees needed — analysis is purely diff-based (read-only)
- No new MCP tools needed — uses four existing tools

---

## Flow

```
Phase 1: GATHER    — fetch all threads + MR diff/data
Phase 2: ANALYZE   — single subagent checks each open thread against the diff
Phase 3: REPORT    — present status table to user, user approves nudge list
Phase 4: NUDGE     — reply on each NOT_APPLIED thread + post summary MR comment
Phase 5: DONE      — no cleanup needed (no worktrees)
```

---

## Thread Verdicts

The analysis agent assigns one verdict per thread:

| Verdict | Meaning | Action |
|---------|---------|--------|
| `APPLIED` | Thread is resolved — trust the resolve action | No action |
| `SILENTLY_APPLIED` | Thread open, but diff shows the concern was addressed | No nudge; optionally note in summary |
| `NOT_APPLIED` | Thread open, diff shows no relevant change | Nudge: thread reply + summary row |
| `NEEDS_HUMAN` | Ambiguous — cannot determine from diff alone | Flag in report; no automatic nudge |

---

## Phase Details

### Phase 1: Gather

1. `mcp__omniforge__fetch_mr_discussions(mr_id, repo_root)` — all threads
2. `mcp__omniforge__fetch_mr_data(mr_id, repo_root)` — diff, commits, files changed, metadata
3. Partition threads:
   - Resolved threads → pre-labeled `APPLIED`, skip analysis
   - Unresolved threads → pass to Phase 2
4. If zero unresolved threads: report "All threads resolved. Nothing to check." and stop.

### Phase 2: Analyze (Single Subagent)

Dispatch ONE analysis subagent with:
- All unresolved threads (structured JSON)
- Full MR diff
- MR metadata (title, author, files changed)

The subagent returns a verdict per thread:
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
  }
]
```

**Large MR handling:** If unresolved thread count > 15 AND diff is large, split into groups by file and dispatch up to 3 subagents. Merge results before Phase 3.

**Agent template:** `./references/analysis-agent-prompt.md`

### Phase 3: Report (User Approval Gate)

Present a status table:

```
OmniCheck — MR !{id}: {title}

  ✓ Applied (resolved):        N threads
  ✓ Silently Applied:          N threads
  ✗ Not Applied:               N threads
  ? Needs Human Review:        N threads

NOT_APPLIED threads:
  1. src/auth.py:47 — Missing null check [confidence: 91%]
  2. .gitlab-ci.yml:1072 — Missing placeholder mapping [confidence: 88%]

NEEDS_HUMAN threads:
  3. general — Unclear if performance concern was addressed

Post nudge replies on NOT_APPLIED threads? [Y/n]
```

**User gate:** No comments are posted until the user explicitly approves. User can remove specific threads from the nudge list before confirming.

### Phase 4: Nudge

For each approved NOT_APPLIED thread:

**Thread reply** (via `mcp__omniforge__reply_to_discussion`):
```
This concern appears unaddressed in the latest changes.

[1-2 sentence summary: what was asked for and what's still missing in the diff]

Could you take another look? Marking for follow-up.
```

**Summary MR comment** (via `mcp__omniforge__post_review_summary`), posted once after all thread replies:
```
## OmniCheck Follow-up — MR !{id}

| Status | Count |
|--------|-------|
| ✓ Applied | N |
| ✓ Silently Applied | N |
| ✗ Not Applied | N |
| ? Needs Human | N |

### Unaddressed Findings

| Thread | File | Summary |
|--------|------|---------|
| #abc123 | src/auth.py:47 | Missing null check on user input |
| #def456 | .gitlab-ci.yml:1072 | Missing placeholder mapping |
```

**Rules:**
- No AI attribution in any posted comment
- Tone is factual, not accusatory
- Thread replies are posted before the summary comment
- If a thread reply fails, continue with remaining threads and note failure in summary

### Phase 5: Done

No worktrees created → no cleanup needed. Report final outcome:
```
OmniCheck complete.
  Nudged: N threads
  Failed to post: N threads (listed)
```

---

## New Files

```
plugins/omniforge/skills/omnicheck-gitlab/
  SKILL.md                          ← main skill orchestration (5 phases)
  references/
    analysis-agent-prompt.md        ← single subagent: threads + diff → verdicts
    nudge-guide.md                  ← exact format for thread replies + summary comment
```

---

## MCP Tools Used

| Tool | Phase | Purpose |
|------|-------|---------|
| `fetch_mr_discussions` | Gather | Get all discussion threads |
| `fetch_mr_data` | Gather | Get diff, commits, metadata |
| `reply_to_discussion` | Nudge | Post per-thread reply |
| `post_review_summary` | Nudge | Post summary MR comment |

No new MCP tools required.

---

## Error Handling

| Error | Response |
|-------|----------|
| `glab` not authenticated | "Run `glab auth login` first." Stop. |
| MR not found | "MR !{id} not found." Stop. |
| No unresolved threads | "All threads resolved. Nothing to check." Stop. |
| Analysis agent fails | Escalate to user; offer to skip nudging or abort |
| Thread reply fails | Continue with remaining threads; report failures in summary |
| Summary comment fails | Report failure; thread replies already posted |

---

## Constraints

- Never post comments without explicit user approval (Phase 3 gate)
- No AI attribution in any posted content
- Use `glab` for GitLab operations (never `gh`)
- No worktrees — diff-only analysis
- Thread replies before summary comment (ordering guarantee)
