# Sweep batched-call prompt (P2)

The ONE model call per sweep. The payload is built by
`omni_sweep.build_batch_prompt(packets)` — this file is the human-readable
contract the code implements (keep them in sync; the code is authoritative).

## Contract

Input: one evidence packet per finding, JSON:

```json
{
  "finding_id": "…",
  "concern": "<the original finding body, capped at 400 chars>",
  "severity": "critical | important | minor",
  "kind": "code | test | docs | process | decision",
  "locus": {"file": "…", "line": 42, "status": "reanchored"},
  "candidate_hunks": [
    {"id": "src/auth.py:H1", "file": "src/auth.py",
     "old_start": 40, "new_start": 40, "lines": ["…capped…"]}
  ],
  "no_evidence": false
}
```

Output: a JSON array ONLY — one object per finding:

```json
[{"finding_id": "…", "verdict": "fixed | not_fixed | needs_judgment",
  "evidence_hunk_id": "<candidate hunk id or null>",
  "confidence": 90, "one_line": "<one sentence>"}]
```

## Rules (stated in the prompt itself)

- A `fixed` verdict MUST cite an `evidence_hunk_id` from that finding's
  `candidate_hunks`. An uncited or out-of-set `fixed` is downgraded to
  `needs_judgment` in code (`apply_citation_rule`) — the model can never
  close a finding on an assertion alone.
- Findings marked `no_evidence` have no candidate hunks: answer
  `not_fixed` or `needs_judgment` — never `fixed`.
- Stay grounded in the hunks; do not speculate about code not shown.
- Every finding in the batch gets a verdict — including empty-evidence ones
  (the operator's "every finding gets an AI verdict" invariant; the prompt
  stays tiny because the hunks are pre-selected and capped).

## Vocabulary

Dispositions, severity, and kind reuse P1's `verdict-model.md` VERBATIM.
The sweep's verdict set (`fixed | not_fixed | needs_judgment`) maps onto it:
`needs_judgment` is the verification state for "cannot decide from the
supplied evidence"; `obsolete` and `stale` are code-derived (re-anchor /
ancestry) and never come from the model.

## Call path (WP0 decision)

The only live implementation tonight is `ClaudeSpawnProvider` — one
`claude -p` spawn per sweep carrying the whole batch (~60 s floor).
`DirectProviderAPI` is a deliberate stub: the direct provider call needs a
secrets/retry story designed in a supervised session, and touches no tokens
tonight.
