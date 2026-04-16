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
- Order NOT_APPLIED threads by confidence descending (highest confidence first)
- If there are zero NOT_APPLIED threads, skip the nudge prompt and just report the status counts

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
| #{discussion_id_short} | {file_location} | {body_summary} |
```

**Field rules:**
- `{discussion_id_short}` — first 7 characters of the discussion ID
- `{file_location}` — format based on available data:
  - Inline thread with line number: `{file_path}:{line_number}`
  - Inline thread without line number: `{file_path}`
  - General (non-inline) thread: `general`
- Include only NOT_APPLIED threads in the "Unaddressed Findings" table — not NEEDS_HUMAN
- If zero NOT_APPLIED threads remain after user exclusions, omit the table and write instead: "All flagged findings have been addressed."
- Do not mention AI, automation, bots, OmniCheck, or OmniForge in this comment

### Failures Section (optional)

If any thread replies failed to post, append this section after "Unaddressed Findings":

```
### Failed to Post

The following nudge replies could not be posted and require manual follow-up:

| Thread | File | Reason |
|--------|------|--------|
| #{discussion_id_short} | {file_location} | {error_summary} |
```

Omit this section entirely if all replies posted successfully.
