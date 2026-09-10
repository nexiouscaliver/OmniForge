# Corpus replay table — verification model v2 (2026-09-11)

Full 55-run replay over the persisted audit corpus (engine repo
claudedocs/omnicheck-audit-2026-09/, read-only). Fixture-subset pins live in
tests/test_p1_replay.py; regenerate this table with:
`python3 skills/omnicheck-gitlab/scripts/replay_audit.py --corpus <corpus>`.

| repo | mr | run | hist gate | audit class | model | oracle | match |
|---|---|---|---|---|---|---|---|
| cleo | !1032 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| cleo | !1067 | 1 | APPROVED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| cleo | !1108 | 1 | APPROVED | FALSE_APPROVE | BLOCKED | BLOCKED | = |
| cleo | !1110 | 1 | APPROVED | CORRECT | CLEAN | CLEAN | = |
| cleo | !1110 | 2 | APPROVED | CORRECT | CLEAN | CLEAN | = |
| cleo | !1114 | 1 | APPROVED | CORRECT | CLEAN | READY_WITH_NOTES | DIFF |
| cleo | !1125 | 1 | APPROVED | CORRECT | CLEAN | CLEAN | = |
| cleo | !1157 | 1 | APPROVED | CORRECT | CLEAN | READY_WITH_NOTES | DIFF |
| cleo | !1158 | 1 | APPROVED | CORRECT | CLEAN | READY_WITH_NOTES | DIFF |
| cleo | !1172 | 1 | APPROVED | FALSE_APPROVE | READY_WITH_NOTES | BLOCKED | DIFF |
| cleo | !1179 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| cleo | !1179 | 2 | APPROVED | FALSE_APPROVE | READY_WITH_NOTES | READY_WITH_NOTES | = |
| cleo | !1180 | 1 | APPROVED | FALSE_APPROVE | BLOCKED | BLOCKED | = |
| cleo | !1184 | 1 | APPROVED | FALSE_APPROVE | BLOCKED | BLOCKED | = |
| cleo | !1194 | 1 | APPROVED | CORRECT | CLEAN | CLEAN | = |
| cleo | !1195 | 1 | REJECTED | CORRECT | BLOCKED | BLOCKED | = |
| cleo | !1196 | 1 | APPROVED | CORRECT | CLEAN | CLEAN | = |
| cleo | !1196 | 2 | APPROVED ( | CORRECT | CLEAN | CLEAN | = |
| cleo | !1205 | 1 | REJECTED | CORRECT | CLEAN | CLEAN | = |
| cleo | !1225 | 1 | APPROVED | CORRECT | CLEAN | CLEAN | = |
| cleo | !1226 | 1 | APPROVED | PARTIAL | BLOCKED | CLEAN | DIFF |
| cleo | !1226 | 2 | APPROVED ( | UNDETERMINABLE | READY_WITH_NOTES | READY_WITH_NOTES | = |
| cleo | !1238 | 1 | APPROVED | CORRECT | CLEAN | CLEAN | = |
| cleo | !1306 | 1 | REJECTED ( | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| cleo | !1357 | 1 | REJECTED ( | CORRECT | BLOCKED | BLOCKED | = |
| cleo | !1427 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| cleo | !1428 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| cleo | !1447 | 1 | REJECTED ( | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| cleo | !356 | 1 | REJECTED | PARTIAL | BLOCKED | BLOCKED | = |
| cleo | !509 | 1 | REJECTED ( | FALSE_REJECT | READY_WITH_NOTES | CLEAN | DIFF |
| cleo | !811 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| cleo | !812 | 1 | APPROVED-s | CORRECT | CLEAN | CLEAN | = |
| loop | !47 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| loop | !47 | 2 | APPROVED | CORRECT | CLEAN | CLEAN | = |
| loop | !48 | 1 | APPROVED | PARTIAL | READY_WITH_NOTES | CLEAN | DIFF |
| loop | !49 | 1 | APPROVED | PARTIAL | READY_WITH_NOTES | CLEAN | DIFF |
| loop | !49 | 2 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| loop | !49 | 3 | APPROVED | CORRECT | CLEAN | CLEAN | = |
| loop | !50 | 1 | APPROVED | CORRECT | READY_WITH_NOTES | CLEAN | DIFF |
| loop | !50 | 2 | APPROVED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| loop | !56 | 1 | APPROVED | CORRECT | CLEAN | READY_WITH_NOTES | DIFF |
| loop | !59 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| loop | !60 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| loop | !61 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| loop | !62 | 1 | REJECTED | CORRECT | BLOCKED | BLOCKED | = |
| loop | !63 | 1 | none | UNDETERMINABLE | READY_WITH_NOTES | READY_WITH_NOTES | = |
| omniforge | !1 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| omniforge | !11 | 1 | APPROVED | CORRECT | CLEAN | CLEAN | = |
| omniforge | !2 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| omniforge | !21 | 1 | APPROVED | CORRECT | CLEAN | CLEAN | = |
| omniforge | !4 | 1 | APPROVED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| omniforge | !5 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| omniforge | !6 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |
| omniforge | !7 | 1 | REJECTED | CORRECT | READY_WITH_NOTES | CLEAN | DIFF |
| omniforge | !8 | 1 | APPROVED | CORRECT | READY_WITH_NOTES | READY_WITH_NOTES | = |

assertion (a) mismatches on CORRECT runs: 6 (blocking-class flips: 0)
assertion (b) cleo!1108: model=BLOCKED oracle=BLOCKED
assertion (b) cleo!1172: model=READY_WITH_NOTES oracle=BLOCKED
assertion (b) cleo!1180: model=BLOCKED oracle=BLOCKED
assertion (b) cleo!1184: model=BLOCKED oracle=BLOCKED
assertion (b) cleo!1226: model=READY_WITH_NOTES oracle=READY_WITH_NOTES
assertion (c) cleo!1306: model=READY_WITH_NOTES blockers=0
assertion (c) cleo!1447: model=READY_WITH_NOTES blockers=0
assertion (c) loop!59: model=READY_WITH_NOTES blockers=0
assertion (c) loop!60: model=READY_WITH_NOTES blockers=0
assertion (c) loop!62: model=BLOCKED blockers=6
