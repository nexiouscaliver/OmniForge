# OmniReview Posting Guide

Reference for Phase 6 — templates, formatting rules, and commands for posting review results.

---

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
