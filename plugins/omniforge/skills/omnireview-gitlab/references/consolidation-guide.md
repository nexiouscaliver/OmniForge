# OmniForge Consolidation Guide

Reference for the OmniForge orchestrator to consolidate findings from all 3 review agents.

Consolidation is deterministic and script-driven. Python never does confidence
arithmetic: every confidence value in the output is byte-identical to an
agent-assigned input — never adjusted, never recomputed. Corroboration is
`count`/`of` metadata only. The orchestrator's job is to run two scripts and
adjudicate ONE generated worklist in a single pass — never to hand-merge,
hand-boost, or severity-pick by itself.

---

## Step 1: Validate Each Waiter Report

Run the validator once per reviewer report (the waiter's files under
`/tmp/omni_wait_out_{id}/`). It extracts the machine-readable findings block
from each report and writes a validated findings file next to it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/omnireview-gitlab/scripts/omni_validate_findings.py" \
  --report /tmp/omni_wait_out_{id}/omni_wait-<transcript-basename>.md \
  --agent codebase \
  --out /tmp/omni_wait_out_{id}/codebase.findings.json
```

Repeat with `--agent security` and `--agent analyst` for the other two reports.
If a validator output says `passthrough: true`, that agent produced no
machine-readable findings — see Degraded mode below.

---

## Step 2: Run the Consolidator

Feed the validated findings files (1–3; degraded runs have fewer) to the
consolidator:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/omnireview-gitlab/scripts/omni_consolidate.py" \
  --findings /tmp/omni_wait_out_{id}/codebase.findings.json \
             /tmp/omni_wait_out_{id}/security.findings.json \
             /tmp/omni_wait_out_{id}/analyst.findings.json \
  --out-dir /tmp/omni_consolidate_{id}
```

`--prior <prior-findings.json>` (optional) supplies previously posted findings
for the retrospective guard — see Already adjudicated below.

Outputs in `--out-dir`:

- `clusters.json` — every finding, grouped into clusters. Per-agent entries are
  preserved VERBATIM inside their cluster (all original fields, byte-identical).
- `worklist.md` — ONE adjudication worklist, consumed in a single pass (Step 4).

### What merges (and only what merges)

Two findings merge into one cluster entry-set ONLY when ALL hold:

1. same normalized file path (lowercased, leading `./` stripped; `null`-file
   findings never merge);
2. overlapping `line_range` intervals (inclusive; `null` ranges never merge);
3. identical normalized category;
4. token-set Jaccard similarity of `one_liner + evidence` ≥ the threshold
   (`--similarity`, default 0.30, calibrated on the W6 fleet).

Everything else is CLUSTERED, never merged: same file + overlapping lines →
one cluster with multiple verbatim entries, regardless of category or
similarity. Non-overlapping or `null`-locus findings are singletons.

### Locus

A cluster's locus is exactly the script's predicate above: the same file with
overlapping line ranges. The canonical anchor format is unchanged:
`relative/path/to/file.ext:line` (the cluster's `locus.anchor`). Findings with
no file locus (commit hygiene, description, process) anchor as `MR-process`.

### Corroboration is metadata only

Each cluster carries `corroboration: {count, of, agents}` — how many of the N
input agents flagged the locus. It is never a score change: confidence values
are never adjusted, never recomputed; cross-perspective discrimination is the
product's core value, so every agent's own score survives verbatim.

---

## Step 3: Confidence Threshold

**Threshold: 70.** The threshold applies to agent-assigned scores ONLY — never
adjusted, never recomputed.

- Findings with confidence >= 70 (the cluster representative's score): eligible
  for the final report and auto-posting
- Findings with confidence 50–69: routed to the worklist's Sub-threshold
  section (include-as-observation vs drop) — never deleted
- Findings with confidence < 50: never surface anywhere (dropped by the script)

False-positive discipline lives where it already belongs: in the reviewer
briefs' own false-positive checks. Agents adjust their OWN confidence before
reporting; python never does it for them.

---

## Step 4: Consume the Worklist in ONE Pass

Read `/tmp/omni_consolidate_{id}/worklist.md` top to bottom, in a single
response, deciding each item from its quoted verbatim entries. The sections
appear in fixed order:

1. **Already adjudicated** — the cluster's locus matches a previously posted
   finding (via `--prior`). Decision is "carry forward as-is", never
   re-adjudication. An OPEN prior thread says `reply on thread <thread_id>` —
   carry the finding forward as a reply on that recorded thread, never a new
   one. A RESOLVED prior says `skip re-posting` — the position is already
   established. (The MR Analyst's Discussion Resolution duty is outside this
   suppression and still reports thread hygiene normally.)
2. **Needs Human Judgment (conflict)** — entries ≥ 2 severity levels apart at
   the same locus. BOTH perspectives are quoted verbatim; the main agent never
   silently resolves. At most ONE verification command per conflict item, and
   only when the quoted evidence is internally contradictory.
3. **Cross-category, same locus** — e.g. a logic bug and its security
   implication: keep both.
4. **Same locus, distinct findings** — same file/lines but different substance
   (below the similarity threshold): keep each perspective; decide each entry's
   inclusion from its quoted evidence.
5. **Sub-threshold observations** — entries scored 50–69 by their agents:
   include as an observation or drop.

This single pass is what caps adjudication turns: no per-item re-verification
loops, no re-deriving subagent evidence, no hand-merging, no severity-picking.
Deduplication IS the consolidator's merge predicate — the main thread never
hand-deduplicates.

### Auto clusters

Clusters with no worklist reason flow straight into the Phase 5 report (no
adjudication needed). They are listed at the bottom of the worklist with their
anchor, representative confidence, and one-liner.

### Degraded mode

Any agent whose validator output says `passthrough: true` produced no
machine-readable findings: consolidate that agent's prose report directly
(pre-3.3.0 behavior — read the report and treat its prose "Finding {N}" blocks
as that agent's findings). Note the fallback in the final report's Summary, and
see the "One Agent Failed" edge case below.

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
| File/Area | MR Analyst (OmniForge) | Codebase (OmniForge) | Security (OmniForge) | Consensus |
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
## OmniForge Report: !{id} — {title}

**Branch:** {source} → {target} | **Author:** {author} | **Pipeline:** {status}
**Reviewed by:** 3 parallel OmniForge agents (MR Analyst, Codebase Reviewer, Security Reviewer)

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

**Note:** When the user selects "Full review post" (option 1) from the action menu, use the **Summary Comment Template** and **Inline Discussion Thread Template** from `./posting-guide.md` to format the posted content. The summary is an overview, the inline threads are technical and actionable — one per finding, never batched.

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
- This is a `conflict` worklist item — the main agent never silently resolves it
- BOTH perspectives verbatim, marked **Needs Human Judgment** — the user decides
- The security agent's attack scenario is quoted as context the code reviewer may not have considered, never as the automatic severity winner
