# Verification model v2 — vocabulary decision (single source of truth)

Decided once in P1 (2026-09-11, from the omnicheck verdict-quality audit and the
stage-hardening decision). P2 (the push sweep) and every later consumer reuse
this vocabulary VERBATIM — never invent a parallel one.

## Per-thread disposition (closed set)

| Disposition | Meaning | Deterministic signal |
|---|---|---|
| `fixed` | The concern is verifiably addressed | Thread resolved with no contrary disclosure, or a verification result (check-mode analysis / sweep verdict) confirms the fix |
| `not_fixed` | Open and unaddressed, no recorded human disposition | Open thread, no consent reply, no verification confirming a fix |
| `obsolete` | The anchored locus was deleted or rewritten out from under the finding | Delta line-map re-anchor (sweep), or premise verifiably invalidated at the current head |
| `deferred` | A human recorded a deferral to a named follow-up (MR / issue / later) | Consent reply: `**Deferred…**`, "deferred to", "follow-up", "separate/later MR", issue ref |
| `declined` | A human declined with rationale | Consent reply: `**SKIPPED**`, `**Acknowledged…**`, `**Answered, …**`, "not changing", "won't fix", "by design" |
| `decision` | The thread holds or awaits a maintainer decision — nothing to apply | Consent/kind signals: "flagged to the operator", "explicit call", scope/split/policy judgment threads |
| `artifact` | Not a finding at all — report posts, briefs, summaries, triggers, bot status | Body shapes below; never classified, never gated, never nudged |

`deferred`, `declined`, and `decision` together form the audit's **DISPOSED**
class (fix 3): reported to humans as ATTENTION_REQUIRED, never a REJECTED
trigger, never a blocker.

A verification state that is missing or unclear (`needs_human` / `needs_judgment`)
is NOT a disposition. An unverified open thread with no consent stays
`not_fixed`; the reason it could not be verified (e.g. truncation) travels with
it as a note.

### Valid consent

Consent = a reply on the thread (never the nudger's own boilerplate — the
"This concern appears unaddressed" template never counts) that records an
explicit human disposition: a deferral to a named follow-up, a decline with
rationale, or a maintainer decision. In production the reply author is checked
against the bot identity when available; in corpus replay (authors stripped)
the body-text signals above are authoritative.

## Severity (closed set)

`critical | important | minor`

Extracted, in priority order:

1. OmniForge posted markers: `**Critical**`, `**Important**`, `**Minor**`
   (prefix match, so `**Critical (…)**` variants count).
2. Structured findings JSON: `severity: critical|important|minor`.
3. regenloop-ship review convention: `**blocking**` → `important`;
   `**non-blocking**` and `**note**` (including `(security)` variants) → `minor`.

Default when no marker is found: `minor`. Severity must be EVIDENCED to gate —
an unmarked thread can never block.

## Kind (closed set)

`code | test | docs | process | decision`

1. `test` — anchor path (or every file referenced) under `tests/`/`test_/`,
   or matching `test_*.py` / `*_test.py` / `*.test.*` / `*.spec.*`.
2. `docs` — anchor path (or every file referenced) ending `.md`, `.rst`, `.txt`.
3. `process` — CI/pipeline/commit-split/MR-scope/approval-count/reviewer-assignment
   concerns with no code locus (phrases: pipeline, CI job, commit split,
   MR split, one-concern, approvals, reviewer).
4. `decision` — maintainer-judgment threads (flagged for an explicit human
   call; scope/policy choices).
5. `code` — everything else (the default for file-anchored findings).

Only `kind == code` can block. `test`, `docs`, `process` findings gate nothing;
they are notes.

## Artifact shapes (never findings)

- OmniForge review report posts (`## OmniForge` + `**Verdict:**`).
- Review-started status notes (`🔄 **OmniForge review started**`).
- Fix briefs (`## OmniForge fix brief`), disposition summaries
  (`## Disposition summary`), consolidated round addenda headers.
- regenloop-ship round summaries (`<!-- regenloop-ship:review:round-N -->`
  followed by `### Review round`).
- Trigger commands: a body that is (ignoring surrounding whitespace) exactly
  `/omnireview`, `/omniforge`, or `/omnicheck`.
- Nudge/verification bot replies ("This concern appears unaddressed…") —
  replies, never findings, never consent.

## The gate (deterministic rule table)

Given the classified findings of a run:

- `BLOCKED` ⟺ at least one finding has disposition `not_fixed` AND severity
  ∈ {`critical`, `important`} AND kind == `code`.
- `CLEAN` ⟺ there are no findings, or every finding is `fixed`.
- `READY_WITH_NOTES` — everything else (open minors, unfixed test/docs/process
  findings, DISPOSED threads awaiting attention, obsolete candidates,
  needs-verification notes with their reasons).

Fail-closed applies to the blocking class only: an unverified
critical/important **code** finding with no consent blocks; everything else
reports as a note. This is what retires the H3 silly-rejection class
(decision threads can no longer gate) without reopening the H5
approve-despite-outstanding class (unanswered Important code still gates).

## approve_mr guards (fix 2 — enforced in the MCP tool)

`approve_mr` refuses (fail-closed, distinct `error_type`s) when:

1. any resolvable thread is still unresolved (`unresolved_threads`),
2. the MR lacks the `omniforge::reviewed` label (`missing_reviewed_label`) —
   the bot approval must never be the thing that certifies an MR OmniForge did
   not review,
3. the head moved since the check (`head_moved`, when the caller passes the
   sha the verification ran against).

## Truncation guard (fix 5)

When the fetched diff was cut by `MAX_DIFF_LINES`/`MAX_DIFF_CHARS`, threads
anchored in files whose regions were cut classify as unverified with the
stated truncation reason (`needs_human: truncated diff`) — never silently
unseen, and the reason appears in the report.
