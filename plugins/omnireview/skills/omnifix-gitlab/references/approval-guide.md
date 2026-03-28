# OmniFix Approval Guide

Reference for Phase 3 — presenting triage results and collecting user approval.

---

### Presentation Format

```
## Triage Results: MR !{id}

### VALID ({N} findings — recommended to fix)

1. {file}:{line} — {short description}
   Confidence: {score} | Proposed fix: {summary}

### INVALID ({N} findings — recommended to skip)

2. {file}:{line} — {short description}
   Reason: {why it's a false positive}

### NEEDS_HUMAN ({N} findings — your call)

3. {file}:{line} — {short description}
   Reason: {what's unclear}

---

What would you like to do?
1. Fix all VALID findings ({N} findings)
2. Select which to fix
3. Fix all including NEEDS_HUMAN ({N} findings)
4. Cancel — don't fix anything
```

### Additional Options

**Auto-resolve fixed threads?**
```
Auto-resolve fixed threads? (Recommended: No — let the original reviewer verify)
1. Yes — resolve threads after posting fix reply
2. No — only post fix reply, let reviewer resolve manually (default)
```

**Commit strategy:**
```
Commit strategy:
1. Single commit for all fixes (default)
2. One commit per fix (easier to revert individually)
```

### User Can

- Approve all VALID — proceed to Phase 4
- Select individual findings — proceed with selection
- Override an INVALID verdict — add it to the fix list
- Edit a proposed fix — modify before applying
- Cancel entirely
