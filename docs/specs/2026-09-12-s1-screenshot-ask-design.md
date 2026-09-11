# S1 — Frontend screenshot ask-the-author flow (plugin side), 2026-09-12

WP-S1, wave 4. Plugin-side only: pure, tested logic + MCP posting helpers.
NO engine changes tonight (the comment-webhook hook that drives reply
detection is wave 5); NO network writes were made while building this.

Source briefs: `claudedocs/plans/2026-09-11-omnicheck-gate-push-sweep-session-briefs.md` §S1
(omniforge repo) and the rev-3 brainstorm Feature 2 (§detection, §label
vocabulary, §ask-author, §posting mechanics).

## What shipped

| Piece | Where | Notes |
|---|---|---|
| Detection classifier | `plugins/omniforge/tools/omni_ui_screenshot.py` | `classify_frontend_change(files)` pure function |
| Label vocabulary | same module | dash-style default, flippable in ONE place |
| Ask-flow state machine | same module | pure decision plans over the ledger record |
| Label add/remove helper | `plugins/omniforge/tools/omniforge_mcp_server.py` | `_update_mr_labels` — one PUT, `add_labels`/`remove_labels` params |
| Note helper | same file | `_post_mr_note` — returns the `discussion_id` |
| Uploads helper | same file | `_upload_project_file` — multipart POST /projects/:id/uploads |
| MCP tools | same file | `update_mr_labels`, `post_mr_note`, `upload_project_file` registered |
| Tests | `tests/test_s1_screenshot_flow.py`, `tests/test_s1_mcp_helpers.py` | 65 + 21 tests, all mocked transport |

Everything is INERT until the engine drives it (wave 5). No skill text was
changed; no call sites exist besides the MCP tool registration.

## Detection (extensions-dominant)

- **strong** — any `.tsx .jsx .vue .svelte` among new/modified files → the
  only level that labels + asks. Strong beats everything else.
- **weak** — only css-family extensions (`.css .scss .sass .less`),
  `package.json`, or path hints (`components/`, `pages/`, `stories/`
  segments; `src/ui/` prefix). Mention-only: never a label, never an ask.
- **none** — no frontend signal; callers must not churn labels.
- **`app/` NEVER signals** — the fleet's FastAPI backends live under `app/`
  (the rev-1 false-positive). Pinned by test: `app/main.py`-only diffs are
  `none`, and weak path hints are suppressed under a top-level `app/`
  segment (`app/pages/render.py` is `none`, not `weak`).

## Label vocabulary (the Premium scoped-label trap)

Defaults: **`omniforge-ui`** (frontend detected) and
**`omniforge-screenshot-requested`** (ask outstanding) — dash-style.

Why: on GitLab Premium, scoped labels sharing a key are mutually exclusive.
`omniforge::ui`, `omniforge::screenshot-requested`, and the existing
`omniforge::reviewed` would all compete for the single `omniforge` scope
key. Harmless on Free today, but the scoped names bake in a Premium
migration hazard. Dash-style names are independent plain labels.

Flip the convention in ONE place: `LABEL_CONVENTION` in
`omni_ui_screenshot.py` (`"dash"` ⇄ `"scoped"` via `labels_for_convention`).
All consumers derive from the module constants.

## Ask-the-author flow (pure state machine)

Ledger record (rides the engine's per-MR ledger file, wave 5):

```json
{"screenshot_state": "none|requested|posted",
 "discussion_id": "",
 "asked_round": null,
 "reasked_round": null}
```

`screenshot_state` records **compliance, not correctness** — nothing is
ever gated on it.

| Event | Function | Rules |
|---|---|---|
| review round | `decide_review_ask(state, round, detection, mr_state, mr_draft)` | terminal MR → stop; draft → wait for Ready flip; strong → ONE ask per round (adds both labels); weak → mention only; posted → no re-ask |
| ask posted | `record_ask(state, round, discussion_id)` | persists the thread id replies match against |
| comment webhook | `detect_image_reply(recorded_id, event_id, body)` | reply must be on the RECORDED discussion id AND the body must match `![…](/uploads/…)` (the note `attachment` boolean is legacy-unreliable — never use it) |
| image detected | `on_image_reply(state)` | state → posted, clear `screenshot-requested`, one 👍 reaction |
| push | `decide_push_reask(state, round, detection, mr_state, mr_draft)` | only a STRONG frontend push AFTER an image landed re-arms: back to requested, re-add the label, ONE re-ask per round on the EXISTING thread; non-frontend/weak pushes never churn anything |

Every function returns a decision plan
`{"action", "reason", "labels_add", "labels_remove", "react", "state"}`
and never mutates its input. The engine persists `plan["state"]` only
AFTER the accompanying post/label call succeeds. Unknown
`screenshot_state` values raise — ledger drift must fail loud.

Asking window ("until Ready→merged"): drafts wait for the Ready flip;
merged/closed/locked MRs never ask again.

## Posting mechanics

- **Labels** — `_update_mr_labels`: ONE `PUT /merge_requests/:iid` with
  `add_labels` / `remove_labels` params (auto-create on add; no
  GET-merge-PUT race, no label pre-creation).
- **Ask note** — `_post_mr_note`: `POST /merge_requests/:iid/notes`
  returning the note's `discussion_id` (the durable thread identity).
- **Uploads** — `_upload_project_file`: `POST /projects/:id/uploads`
  returns `{markdown, url, full_url}`; the markdown embeds verbatim in
  notes. 100 MiB platform limit enforced client-side.

### The glab multipart finding (verified 2026-09-12)

Installed glab **1.67.0** cannot express multipart/form-data:
- no `--form` flag (`unknown flag: --form`, probed);
- `--field`/`--raw-field` values serialize as JSON string params (source:
  archived profclems/glab `commands/api/http.go` — `json.Marshal`), and the
  official `--form` multipart flag documented on docs.gitlab.com is newer
  than this install (GitLab work item #8229 tracks the gap).

Workaround shipped: `build_multipart_upload_body()` builds the multipart
bytes locally (fixed boundary, boundary-collision guard, mimetype guess)
and `_upload_project_file` sends them via `glab api --input <tmpfile>` with
an explicit `-H "Content-Type: multipart/form-data; boundary=…"`. The temp
body file is always removed.

**Residual risk for wave 5:** whether glab 1.67's `--input` preserves our
Content-Type header (vs forcing its own) could not be verified without a
network write. First live use must confirm; if the header is overridden,
regenerate the boundary from the actual response error or upgrade glab
(≥ the release that added `--form`).

## Engine handoff contract (wave 5)

The engine drives; the plugin decides. On each event, call the pure
function, then execute the emitted actions via the MCP helpers:

1. review round on an MR → classify changed files → `decide_review_ask`
   → if `ask`: `post_mr_note` the ask → `update_mr_labels(add=…)` →
   `record_ask` and persist the plan state to the ledger.
2. comment webhook → `detect_image_reply(ledger.discussion_id, …)` → if
   true: `on_image_reply` → `update_mr_labels(remove=…)` + one reaction →
   persist.
3. push webhook → classify the pushed files → `decide_push_reask` → if
   `reask`: reply on the existing thread + `update_mr_labels(add=…)` →
   persist.

Rate-limit note (gitlab.com): notes 60/min — the flow posts at most one
ask + one re-ask per review round by construction.
