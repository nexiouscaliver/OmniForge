# OmniFix Commit and Post Guide

Reference for Phase 6 — committing fixes, pushing, posting thread replies, and resolving discussions.

---

### Race Condition Check

Before pushing, check for source branch drift:
1. `git fetch origin {source_branch}` in the fix worktree
2. Compare `origin/{source_branch}` HEAD with the worktree's base commit
3. If they differ:
   - Warn: "Source branch has {N} new commits since we started."
   - Offer: (a) rebase fixes on new HEAD, (b) abort push, (c) create a separate MR
4. **Never force-push.** If rebase has conflicts, abort and let user resolve manually.

### Commit

Stage and commit in the fix worktree:

```bash
# In .worktrees/omnifix-{mr_id}
# Worktrees may not have .pre-commit-config.yaml — allow missing config
PRE_COMMIT_ALLOW_NO_CONFIG=1 git add -A
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "fix: resolve {N} review findings from MR !{mr_id}

Fixes:
- {file}:{line} — {description}
- {file}:{line} — {description}"
```

**Note:** `PRE_COMMIT_ALLOW_NO_CONFIG=1` is needed because worktrees share `.git` config but not workspace files. The pre-commit hook runs but can't find its config in the worktree directory. This is safe — the main repo's pre-commit will validate on the next commit there.

No mention of OmniFix, AI, or automation in the commit message.

### Push

**Ask user before pushing — never auto-push.**

If approved:
```bash
git push origin omnifix-temp-{mr_id}:{source_branch}
```

### Post Thread Replies

For each fixed finding, follow the `_post_full_review` error-handling pattern — collect errors per thread, continue with remaining, report summary:

```
mcp__omniforge__reply_to_discussion(
    mr_id="{id}",
    discussion_id="{discussion_id}",
    body="Fixed in commit {sha}: {description}",
    repo_root="{cwd}"
)
```

Retry once for 5xx errors with 2-second delay.

### Resolve Threads (if user opted in at Phase 3)

```
mcp__omniforge__resolve_discussion(
    mr_id="{id}",
    discussion_id="{discussion_id}",
    resolved=true,
    repo_root="{cwd}"
)
```

### Summary Comment

Post a summary comment on the MR:

```markdown
## OmniFix Summary: {N} findings processed

| Finding | File | Status |
|---------|------|--------|
| {description} | {file}:{line} | Fixed in {sha} |
| {description} | {file}:{line} | Skipped ({reason}) |
```

No AI attribution in any posted content.

### Post Results

```
Reply results: {N}/{M} succeeded
Resolve results: {N}/{M} succeeded
Errors: [list with discussion_id and error message]
```
