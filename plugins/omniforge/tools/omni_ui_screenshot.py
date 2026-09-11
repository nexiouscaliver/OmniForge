"""Pure logic for the S1 frontend-screenshot ask-the-author flow (WP-S1).

This module is the plugin side of the S1 feature: frontend-change detection,
the screenshot label vocabulary, and the ask-the-author flow state machine.
Everything here is PURE (no I/O, no network) so the engine can drive it from
its webhook/ledger side in wave 5; the MCP posting helpers that execute the
emitted actions live in tools/omniforge_mcp_server.py.

Detection (extensions-dominant)
-------------------------------
The strong signal is FILE EXTENSIONS: .tsx .jsx .vue .svelte on new/modified
files. Paths (components/ pages/ src/ui stories/) are a WEAK tiebreaker only -
they produce a "mention", never the label or the ask. `app/` is NEVER a
signal: the fleet's FastAPI backends live under app/ and pattern-matching it
false-positives on every backend MR (this negative is pinned by test).

Label vocabulary
----------------
Defaults are DASH-STYLE: omniforge-ui and omniforge-screenshot-requested.
Rationale: GitLab Premium scoped labels (key::value) sharing a key are
mutually exclusive - omniforge::ui, omniforge::screenshot-requested and
omniforge::reviewed would all compete for the single "omniforge" scope key.
Harmless on Free today, but the scoped names bake in a Premium migration
hazard. Dash-style names are independent plain labels. To flip the
convention for the whole flow, change LABEL_CONVENTION below (one place).

Ask-the-author flow
-------------------
One bot thread asks the author for a screenshot of the rendered change.
Replies are detected by regexing note bodies for image markdown pointing at
/uploads/ on the RECORDED discussion id (the note `attachment` boolean is
legacy-unreliable and must not be used). State lives in the engine ledger
("screenshot_state"); it records COMPLIANCE, not correctness - nothing is
ever gated on it. Asks are capped at one per review round, re-asks (fired by
a frontend-touching push after an image landed) at one per round; draft MRs
wait for the Ready flip; merged/closed MRs never ask again.
"""

# -- Label vocabulary ------------------------------------------------------

LABEL_CONVENTION_DASH = "dash"
LABEL_CONVENTION_SCOPED = "scoped"

#: Flip the naming convention HERE - the only place it is decided.
#: Dash-style avoids the Premium scoped-label mutual-exclusion trap.
LABEL_CONVENTION = LABEL_CONVENTION_DASH


def labels_for_convention(convention):
    """Return (ui_label, screenshot_requested_label) for a convention.

    The one place the label naming decision lives; every consumer derives
    from the module constants, never hardcodes label strings.
    """
    if convention == LABEL_CONVENTION_DASH:
        return ("omniforge-ui", "omniforge-screenshot-requested")
    if convention == LABEL_CONVENTION_SCOPED:
        return ("omniforge::ui", "omniforge::screenshot-requested")
    raise ValueError(
        f"Unknown label convention: {convention!r}. "
        f"Use 'dash' or 'scoped'.")


UI_LABEL, SCREENSHOT_REQUESTED_LABEL = labels_for_convention(LABEL_CONVENTION)
ALL_FLOW_LABELS = (UI_LABEL, SCREENSHOT_REQUESTED_LABEL)


# -- Detection (extensions-dominant) ---------------------------------------

#: Extensions that mean "frontend change" on their own (new/modified files).
FRONTEND_EXTENSIONS = (".tsx", ".jsx", ".vue", ".svelte")

#: Weak signals - worth a mention in the ask, never enough to trigger it.
WEAK_EXTENSIONS = (".css", ".scss", ".sass", ".less")
WEAK_FILENAMES = ("package.json",)
WEAK_PATH_SEGMENTS = ("components", "pages", "stories")
WEAK_PATH_PREFIXES = ("src/ui",)

#: Deliberately NOT a signal, in any form: "app" (segment) or "app/" prefix.
#: The fleet's FastAPI backends live under app/ and would false-positive.


def _has_strong_signal(path):
    return path.lower().endswith(FRONTEND_EXTENSIONS)


def _has_weak_signal(path):
    lowered = path.lower()
    if lowered.endswith(WEAK_EXTENSIONS):
        return True
    segments = [s for s in lowered.split("/") if s]
    if not segments:
        return False
    if segments[-1] in WEAK_FILENAMES:
        return True
    # A top-level app/ directory confers nothing, even for path hints -
    # the fleet's FastAPI backends live there (app/pages/render.py is a
    # backend module, not a frontend page).
    if segments[0] == "app":
        return False
    if any(seg in WEAK_PATH_SEGMENTS for seg in segments):
        return True
    return any(lowered.startswith(p + "/") for p in WEAK_PATH_PREFIXES)


def classify_frontend_change(changed_files):
    """Classify a changed-file list (new/modified paths) as frontend work.

    Returns {"level": "strong"|"weak"|"none",
             "matched": [...strong files...],
             "weak_matched": [...weak-only files...]}.

    "strong" -> at least one .tsx/.jsx/.vue/.svelte file: label + ask flow.
    "weak"   -> only path/style hints (css, package.json, components/...):
                mention only, NEVER a label or an ask.
    "none"   -> no frontend signal; callers must not churn labels.
    """
    matched = []
    weak_matched = []
    for path in changed_files or []:
        if not path:
            continue
        if _has_strong_signal(path):
            matched.append(path)
        elif _has_weak_signal(path):
            weak_matched.append(path)
    if matched:
        level = "strong"
    elif weak_matched:
        level = "weak"
    else:
        level = "none"
    return {"level": level, "matched": matched, "weak_matched": weak_matched}


# -- Ask-the-author flow state machine (stub - RED) ----------

STATE_NONE = "none"
STATE_REQUESTED = "requested"
STATE_POSTED = "posted"


def new_state():
    raise NotImplementedError


def validate_state(state):
    raise NotImplementedError


def decide_review_ask(state, round, detection, mr_state="opened", mr_draft=False):
    raise NotImplementedError


def record_ask(state, round, discussion_id):
    raise NotImplementedError


def note_has_upload_image(body):
    raise NotImplementedError


def detect_image_reply(recorded_discussion_id, event_discussion_id, note_body):
    raise NotImplementedError


def on_image_reply(state):
    raise NotImplementedError


def decide_push_reask(state, round, detection, mr_state="opened", mr_draft=False):
    raise NotImplementedError
