# OmniForge Posting Guide

Reference for Phase 6 — templates, formatting rules, and commands for posting review results.

---

### Option 1: Full Review Post (Recommended)

This is the most common action. It posts everything in one go:

1. Post the **summary comment** (overview template below) as a top-level MR note.
2. For **EACH finding** with confidence >= 70, post a separate **inline discussion thread** on the relevant diff line.
3. Report back: "Posted summary comment + {N} inline discussion threads"

**Do NOT batch findings.** Each finding gets its own thread so the MR author can resolve them independently. Multiple threads on the same file = expected and encouraged.

**Implementation:** use the shipped `omni_post_review.py` (the script wraps the direct GitLab Discussions API — anchored inline threads, retry/backoff, reply routing, duplicate-summary guard).

**Line numbers:** Use the `diff_line_map` from the `fetch_mr_data` response (Phase 1) to get valid line numbers for each file. The `added_lines` array contains exact line numbers where code was added — use these for `line_number` in findings. Do NOT call `map_diff_lines` separately — the data is already available from Phase 1.

### Summary Comment Template

The summary is an **overview** — a high-level snapshot for anyone (author, reviewer, PM) to quickly understand the MR state. Keep it concise. The detail lives in the inline threads.

```markdown
## OmniForge

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

#### Shipped script:

Run the shipped `scripts/omni_post_review.py` for the summary note and all inline threads —
anchored via the position payload, with retry/backoff, reply routing, the duplicate-summary
guard, and `--dry-run` built in. For partial-failure resume use
`omni_post_review.py --skip-summary --force` as described below.

The script adds `--reply-to <thread_id>` / per-entry `reply_to_thread_id` for carrying forward
OPEN prior findings (replies on the recorded thread — never a new one; resolved priors are
skipped by the caller before the array is prepared). It accepts the SAME findings array as
`post_full_review`. The array shape matches for new-thread entries, but `reply_to_thread_id`
is script-only routing: MCP `_post_full_review` posts every entry as a NEW inline thread (a
reply entry sent via MCP would wrongly create a new thread), so MCP runs must send replies via
`mcp__omniforge__reply_to_discussion` instead:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/omnireview-gitlab/scripts/omni_post_review.py" \
  --mr {iid} --project {project-id-or-fullpath} \
  --summary /tmp/omni_review_{id}_summary.md \
  --findings-json /tmp/omni_review_{id}_findings.json \
  --since {run-start-epoch}
```

Exit codes:

| Exit | Meaning |
|------|---------|
| `0` | success — everything planned was posted |
| `1` | posting failure after retries, or a 4xx fail-fast (stderr names the failing glab command) |
| `2` | usage error — bad flags or malformed findings JSON; prints no stdout JSON |
| `3` | duplicate-summary guard refusal — nothing was posted |

**Note entries (3.3.2):** findings with no diff locus (MR-meta process notes, security posture) go in the same
findings array as note entries — objects carrying ONLY `{"body": ...}` (no `file_path`/`line_number`). The
script posts them as top-level MR notes AFTER summary/threads/replies, counts them in the stdout `notes` key,
and validates the shape strictly: a body-only entry that also carries `file_path`, `line_number`, `old_path`, or
`old_line` is a usage error (exit 2 — ambiguous shape, never silently skipped). Note entries replace the raw-glab general-notes
path: `glab api --input -` silently drops note bodies (production: MR !1388 lost 5/5 notes to it), so meta
findings with no diff locus must go through the poster. Like `reply_to_thread_id`, note entries are
script-only routing — MCP `_post_full_review` rejects body-only entries as invalid findings and fails the
whole call, so interactive MCP installs must not receive body-only entries. A batch with no new-thread entries (replies and/or notes only)
may omit `--summary` entirely: the run takes the implied skip — no summary note, duplicate-summary guard not
evaluated. On a pure-deletion MR (zero added lines) or for any finding whose locus has no anchorable diff line, plan a note entry —
or a reply on the matching prior thread — never an inline thread: GitLab rejects unanchorable positions (`400 line_code can't be blank`),
and that failed thread post aborts the whole batch.

**Optional rich keys + the automatic fix brief (R2-P):** thread and note entries MAY
additionally carry `severity` (`"critical"|"important"|"minor"`), `title`, `category`,
`problem`, `recommendation` (strings; all optional; never validated — unknown keys are
never usage errors). When present they enrich the automatic fix brief; when absent the
brief derives `severity`/`title` from the body's `**Severity**` marker line, `problem`
from the full body, and renders `none given — derive from the problem statement` for a
missing recommendation. Reply entries never enter the brief. More than 25 findings →
the top 25 by severity plus a pointer line.

After any run that posts ≥1 new thread or note entry, the poster appends ONE final
general MR note — the OmniForge fix brief (rendered by `scripts/omni_fixprompt.py`,
paste-ready for a coding agent). It is always the LAST artifact posted. The stdout JSON
line carries two additive keys: `fix_brief` (bool — whether the brief posted) and
`thread_map` (object mapping `"<findings-array-index>"` → that entry's permalink
`<web_url>#note_<id>`; after a mid-batch exit 1 it lists exactly the artifacts that
succeeded — the manual-reconstruction source for a full-set brief on resume). The brief
is skipped automatically (one stderr line, exit 0) on zero-finding runs, reply-only /
`--reply-to` runs, a MR-meta GET failure on notes-only batches, POST responses that
yield no note id, or missing MR meta fields — never a stale brief.

Rich keys (like note entries and `reply_to_thread_id`) are script-path-only: MCP
`_post_full_review` compatibility is unchanged, and MCP behavior on unknown entry keys
is out of scope here.

**Partial-failure resume (exit 1 mid-batch):** the summary posts first, then threads in array order, failing
fast — after a mid-batch exit 1 the summary and the leading threads are already on the MR. NEVER rerun the same
command with `--force`: it skips the guard, reposts the summary, and duplicates every already-posted thread. A
plain rerun is refused by the guard (exit 3 — this run's own summary is newer than `--since`); that is the guard
working. Re-post ONLY the remaining entries: `omni_post_review.py --skip-summary --force` with the findings
array edited down to the unposted entries (never a plain rerun with `--force` — it reposts the summary). A
script rerun is safe only when nothing was posted yet (failure during the diff-refs fetch or the summary post —
plain rerun, no `--force` needed).

**Fallback (no MCP server):**

| Action | Command |
|--------|---------|
| Summary | `glab mr note {id} -m "{summary}"` |
| Create issue | `glab issue create -t "[MR !{id}] {title}" -d "{desc}" --linked-mr {id} --no-editor` |
| Approve | `glab mr approve {id}` |
| Open browser | `glab mr view {id} -w` |

**Optional: interactive MCP installs**

**If MCP tools are available** (recommended — handles all GitLab API complexity automatically):

| Action | MCP Tool |
|--------|----------|
| Full review post | `mcp__omniforge__post_full_review(mr_id, summary, findings_json, repo_root)` |
| Summary only | `mcp__omniforge__post_review_summary(mr_id, summary, repo_root)` |
| Inline thread | `mcp__omniforge__post_inline_thread(mr_id, file_path, line_number, body, repo_root)` |
| Create issue | `mcp__omniforge__create_linked_issue(mr_id, title, description, labels, repo_root)` |

For `post_full_review`, the `findings` parameter is a JSON string of an array:
```json
[
  {"file_path": "src/app.py", "line_number": 42, "body": "**Important** — Missing null check\n\n**What:** ..."},
  {"file_path": "src/app.py", "line_number": 87, "body": "**Minor** — Magic number\n\n**What:** ..."}
]
```

The MCP tools automatically fetch diff position SHAs, URL-encode the project path, and construct the GitLab API request. The model only needs to provide the text and line numbers.

Open-prior replies in interactive MCP installs use `mcp__omniforge__reply_to_discussion`. The script is
standalone (stdlib only, glab subprocess only) so posting still works when the MCP server cannot start.

**No AI attribution in any posted content.** Write as a standard code review comment.

---

## det-scan evidence posting (omni_det_scan.py)

Scanner-evidence posting is a separate flow from the `## OmniForge` verdict artifacts
above: `scripts/omni_det_scan.py` consumes the engine's det-scan v1 evidence packet and
posts the evidence directly — the scanner is evidence, the model is the verdict. Every
body it posts starts `det-scan:`; the threads carry disposition `needs_judgment` and are
NEVER auto-resolved by any plugin component — the review agents adjudicate them like any
other finding. The flow NEVER touches GitLab labels.

**Flags:** `--packet` (path to the evidence packet) · `--project` · `--mr` · `--since`
(engine scan-round epoch; the skill passes `"${OMNIFORGE_DET_SCAN_EPOCH:-0}"`) ·
`--host` · `--force` · `--dry-run` · `--packet-epoch` (consumer-level packet mtime
override) · `--attempts` · `--backoff-base`. The token comes from `GITLAB_TOKEN` /
`OMNIFORGE_GITLAB_TOKEN` env only — there is no flag for it.

**Exit codes:**

| Exit | Meaning |
|------|---------|
| `0` | posted, or scan-skipped (benign no-op — receipt `action="skipped"`) |
| `1` | posting failure after retries — prior artifacts stay |
| `2` | argparse usage, or validation: `unknown-top-level-field:<k>`, `unsupported-schema-version`, `packet-not-json`, `packet-unreadable`, `bad-field:<locus>`, `packet-mr-mismatch`, or missing token |
| `3` | refusal: `stale-packet`, `det-scan-already-posted`, `head-sha-mismatch` |

**Exit 1 (mid-batch posting failure):** resolve manually using the receipt counts and
thread ids; do NOT `--force` to resume — it re-posts everything. On every exit 1 the
receipt's `action` field still reads `"posted"` — the counts
(`posted_summary`/`threads`/`failures`) carry the truth.

**Epoch-absent consequence:** with no engine epoch, `--since 0` makes the dedup guard
refuse ANY re-post on re-reviews — safe: refuse beats duplicate, and the receipt surfaces
it.

**Summary note template** (posted to `.../notes`):

```markdown
det-scan: scanner evidence — MR !{iid} @ {head_sha first 8}

**Scan status:** {scan.status} — {scan.reason}

**Tools:**
- {name} {version} — {status} ({duration_s}s)

**Findings:** {N} total · {A} anchored (threads below) · {U} unanchored (see below)
**Capped:** {meta.capped} · overflow not adjudicated: {meta.overflow_not_adjudicated}
**Redaction:** {meta.redaction}

**How to read this:** the scanner is evidence, the model is the verdict. Every `det-scan:`
thread below carries disposition `needs_judgment` and is adjudicated by the review agents —
this note is not a verdict and these threads are never auto-resolved.

### Unanchored evidence (no anchorable diff line — adjudicate from the file)
- [{tool}] {rule_id} — {file}:{line} — severity {severity} — {preview_redacted}
```

The `### Unanchored evidence` section (heading + list) renders ONLY when U > 0.

**Finding thread body template** (posted to `.../discussions`, anchored at `{file}`/`{line}`):

```markdown
det-scan: [{tool}] {rule_id} — {file}:{line}

**{Critical|Important|Minor}** — scanner severity {severity} · class {class}

{preview_redacted}

**Disposition: needs_judgment** — scanner evidence, not a verdict. This thread is
adjudicated by the review agents and is never auto-resolved.
```
