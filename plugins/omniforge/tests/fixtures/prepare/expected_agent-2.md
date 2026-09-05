# OmniForge reviewer brief — Codebase Reviewer (agent-2)

- Review ID: rev-1
- Project: 73279395
- MR: !21 — Add widget API
- Branches: feat/widget → main
- Head SHA: head1
- Generated: 2026-09-04T12:00:00+00:00

## Owned files (deep-dive ownership)

Deep-dive owner: these files:

- `src/app.py` — 3 added lines — greedy-balance
- `src/net.py` — 1 added lines — greedy-balance

Cross-cutting: you still sweep ALL changed files at grep depth; full-file reads are your
owned files only.

## Cross-cutting files (all 4 changed files)

Cross-cutting: all 4 changed files (see partition.json)

## Stats

- Owned: 2 files / 4 added lines
- MR total: 4 files / 9 added lines

## Dispatch note

The orchestrator fills `{OWNED_FILES}` in the reference template with this brief's
"Owned files" section above (owned list + both depth sentences) — nothing else from this
file. Worktree path is assigned at dispatch (Phase 2).
