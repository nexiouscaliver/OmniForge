# OmniReview Consolidation Guide

Reference for the OmniReview orchestrator to consolidate findings from all 3 review agents.

---

## Step 1: Parse Agent Reports

Extract structured findings from each agent's report into a normalized format:

```
{
  source_agent: "MR Analyst (OmniReview)" | "Codebase Reviewer (OmniReview)" | "Security Reviewer (OmniReview)",
  category: string,
  severity: "critical" | "important" | "minor",
  confidence: number (0-100),
  location: string (file:line or commit SHA or "MR description"),
  description: string,
  evidence: string,
  recommendation: string,
  impact: string (optional, from security agent),
  attack_scenario: string (optional, from security agent)
}
```

---

## Step 2: Apply Confidence Threshold

**Threshold: 70**

- Findings with confidence >= 70: Include in final report
- Findings with confidence 50-69: Mention in a "Lower Confidence Observations" appendix (optional, only if user asks)
- Findings with confidence < 50: Discard completely

---

## Step 3: Cross-Correlation

### 3a. Normalize Locations

Convert all file:line references to canonical form: `relative/path/to/file.ext:line_number`

### 3b. Proximity Grouping

Findings are in the "same area" if:
- Same file AND within 5 lines of each other
- Same commit SHA (for MR analyst findings)
- Same discussion thread

### 3c. Boost Correlated Findings

When multiple agents flag the same area:

| Agents Agreeing | Confidence Boost | Cap |
|----------------|-----------------|-----|
| 2 agents | +15 | 100 |
| 3 agents | +25 | 100 |

### 3d. Handle Contradictions

When agents reach different conclusions about the same area:
- Present BOTH perspectives in the report
- Mark as **"Needs Human Judgment"**
- Do NOT silently resolve — the user decides
- List which agent said what with their evidence

---

## Step 4: Deduplication

When findings overlap:

1. **Same issue, same severity:** Merge into one finding. List all source agents. Use the most detailed description and evidence.
2. **Same area, different severity:** Keep the HIGHEST severity. Note the range of opinions.
3. **Same area, different categories:** Keep both if they add unique value (e.g., one is a logic bug, the other is a security implication of that bug). Merge if redundant.
4. **Unique findings:** Keep as-is with the original agent's confidence.

---

## Step 5: Sort and Organize

### Primary sort: Severity
1. Critical (must fix before merge)
2. Important (should fix)
3. Minor (nice to have)

### Secondary sort: Confidence (descending)
Within each severity level, highest confidence first.

### Group by category for readability
Within each severity level, group related findings together.

---

## Step 6: Build Agent Agreement Matrix

For each file touched by the MR, create a row showing what each agent found:

```markdown
| File/Area | MR Analyst (OmniReview) | Codebase (OmniReview) | Security (OmniReview) | Consensus |
|-----------|------------------------|----------------------|----------------------|-----------|
| service.py | - | Important: missing error handling | Important: unvalidated input | Both flagged (high confidence) |
| config.yml | Minor: unclear description | - | Critical: exposed secret | Security concern |
| test_service.py | - | Minor: missing edge case test | - | Single finding |
```

This matrix makes gaps visible. If an agent found nothing for a file, show `-`. If the file wasn't in their scope, show `N/A`.

---

## Step 7: Compose Final Report

Use this template:

```markdown
## OmniReview Report: !{id} — {title}

**Branch:** {source} → {target} | **Author:** {author} | **Pipeline:** {status}
**Reviewed by:** 3 parallel OmniReview agents (MR Analyst, Codebase Reviewer, Security Reviewer)

### Summary
[1-3 sentences: overall assessment, key risk areas, recommendation]

### Verdict: [APPROVE | APPROVE_WITH_FIXES | REQUEST_CHANGES | BLOCK]

**Reasoning:** [1-2 sentences explaining the verdict]

---

### Strengths
[Merged from all 3 agents — deduplicated, most impactful first]

---

### Issues

#### Critical (Must Fix Before Merge)
[Each finding with: file:line | description | evidence | recommendation | confidence | source agent(s)]

#### Important (Should Fix)
[Same format]

#### Minor (Nice to Have)
[Same format]

---

### MR Process Notes
[From MR Analyst: commit quality, description, discussions. Summarize key points.]

### Security Assessment
[From Security Reviewer: posture assessment, OWASP categories checked, overall risk level]

### Recommendations
[Future improvements beyond this MR's scope — from any agent]

---

### Agent Agreement Matrix
[Table from Step 6]

---

**Findings Summary:** {N} Critical, {N} Important, {N} Minor | **Confidence range:** {min}-{max}
```

**Note:** When the user selects "Full review post" (option 1) from the action menu, use the **Summary Comment Template** and **Inline Discussion Thread Template** from SKILL.md Phase 6 to format the posted content. The summary is an overview, the inline threads are technical and actionable — one per finding, never batched.

---

## Verdict Decision Logic

| Condition | Verdict |
|-----------|---------|
| No Critical or Important findings | APPROVE |
| No Critical, some Important (all easily fixable) | APPROVE_WITH_FIXES |
| Critical findings OR many Important findings | REQUEST_CHANGES |
| Critical security vulnerability with active exploit path | BLOCK |

---

## Edge Cases

### One Agent Failed
- Note the gap prominently: "Note: {agent} did not complete. {domain} review is incomplete."
- Still present findings from the other 2 agents
- Recommend the user pay extra attention to the missing domain

### All Agents Found Nothing
- This is suspicious for any non-trivial MR
- Double-check that agents actually explored the codebase (they should list files they read)
- If legitimate: "No issues found. All 3 agents independently verified the changes."
- Still present strengths and the agreement matrix

### Massive Number of Findings
- If > 20 findings after filtering: Present top 10 by severity/confidence
- Append "Additional {N} findings available — ask to see the full list"
- This prevents overwhelming the user

### Security Finding Contradicts Code Review
- Security always wins for severity classification
- Present both perspectives
- The security agent's attack scenario adds context the code reviewer may not have considered
