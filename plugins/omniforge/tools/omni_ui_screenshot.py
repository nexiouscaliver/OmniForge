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
they produce a "mention", never the label or the ask. A top-level app/
directory confers nothing (path hints are suppressed under it): the fleet's
FastAPI backends live under app/ and pattern-matching it false-positives on
every backend MR (this negative is pinned by test). Weak stylesheets and
package.json remain weak (mention-only) even under app/.

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
ONE bot thread asks the author for a screenshot of the rendered change:
the first ask opens the thread; every later ask (new review round, push
re-arm) posts as a REPLY on it, and record_ask never replaces a recorded
discussion id - so a late image reply on the original thread always
matches. Replies are detected by regexing note bodies for image markdown
pointing at /uploads/ on the RECORDED discussion id (the note `attachment`
boolean is legacy-unreliable and must not be used). State lives in the engine ledger
("screenshot_state"); it records COMPLIANCE, not correctness - nothing is
ever gated on it. Asks are capped at one per review round, re-asks (fired by
a frontend-touching push after an image landed) at one per round; draft MRs
wait for the Ready flip; merged/closed MRs never ask again.
"""

import re

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


# -- Ask-the-author flow state machine ------------------------
#
# Pure state logic over the engine-ledger screenshot record. The engine
# (wave 5) drives the events; every function here returns a DECISION PLAN
# {"action", "reason", "labels_add", "labels_remove", "react", "state"}
# and NEVER mutates its input. Plans describe intent: the engine persists
# plan["state"] only AFTER the accompanying post/label call succeeds.
#
# Asking window: draft MRs wait for the Ready flip; merged/closed/locked
# MRs never ask again ("until Ready->merged" in the brief). screenshot_state
# records COMPLIANCE only - nothing is ever gated on it.

STATE_NONE = "none"
STATE_REQUESTED = "requested"
STATE_POSTED = "posted"

_VALID_STATES = (STATE_NONE, STATE_REQUESTED, STATE_POSTED)

#: MR states after which no ask or re-ask may ever fire again.
TERMINAL_MR_STATES = ("merged", "closed", "locked")

#: Image markdown pointing at GitLab uploads: ![alt](.../uploads/...).
#: The note `attachment` boolean is legacy-unreliable - never use it.
IMAGE_UPLOAD_RE = re.compile(r"!\[[^\]]*\]\([^)]*/uploads/[^)]+\)")


def _plan(action, reason, state, labels_add=(), labels_remove=(),
          react=False, post_as=None):
    return {
        "action": action,
        "reason": reason,
        "labels_add": list(labels_add),
        "labels_remove": list(labels_remove),
        "react": react,
        "state": dict(state),
        "post_as": post_as,
    }


def new_state():
    """A fresh screenshot ledger record (the engine's screenshot_state
    keys ride its per-MR ledger file)."""
    return {
        "screenshot_state": STATE_NONE,
        "discussion_id": "",
        "asked_round": None,
        "reasked_round": None,
    }


def validate_state(state):
    """Fail loud on ledger drift - a malformed record must never silently
    no-op the flow."""
    if not isinstance(state, dict) or "screenshot_state" not in state:
        raise ValueError(
            "screenshot state record must be a dict with a "
            "'screenshot_state' key; got: %r" % (state,))
    if state["screenshot_state"] not in _VALID_STATES:
        raise ValueError(
            "unknown screenshot_state %r (expected one of %s)"
            % (state["screenshot_state"], ", ".join(_VALID_STATES)))
    return state


def _require_round(round):
    """Rounds are 1-based ints from the engine ledger; anything else is
    ledger drift and must fail loud (never compare across types)."""
    if not isinstance(round, int) or isinstance(round, bool) or round < 1:
        raise ValueError(
            "round must be a positive integer; got: %r" % (round,))
    return round


def _asked_this_round(state, round):
    return state.get("asked_round") is not None and state["asked_round"] >= round


def _reasked_this_round(state, round):
    return (state.get("reasked_round") is not None
            and state["reasked_round"] >= round)


def decide_review_ask(state, round, detection, mr_state="opened",
                      mr_draft=False):
    """Decide the review-time ask: one bot thread per review round.

    Called when a review round runs on the MR. Only a STRONG detection
    asks; weak detections are mention-only (never labels); drafts wait for
    the Ready flip; terminal MRs never ask; an image already posted stays
    posted until a frontend push re-arms it (decide_push_reask).
    """
    validate_state(state)
    _require_round(round)
    if mr_state.lower() in TERMINAL_MR_STATES:
        return _plan("none", "mr_terminal", state)
    if mr_draft:
        return _plan("none", "draft_wait", state)
    level = (detection or {}).get("level", "none")
    if level == "none":
        return _plan("none", "not_frontend", state)
    if level == "weak":
        return _plan("none", "weak_frontend_mention_only", state)
    if state["screenshot_state"] == STATE_POSTED:
        return _plan("none", "already_posted", state)
    if _asked_this_round(state, round):
        return _plan("none", "already_asked_this_round", state)
    # One bot thread: the first ask opens it; later-round asks ride it.
    if state["screenshot_state"] == STATE_REQUESTED and state["discussion_id"]:
        return _plan("ask", "round_reask_existing_thread", state,
                     labels_add=(UI_LABEL, SCREENSHOT_REQUESTED_LABEL),
                     post_as="reply_on_recorded_thread")
    return _plan("ask", "frontend_round_ask", state,
                 labels_add=(UI_LABEL, SCREENSHOT_REQUESTED_LABEL),
                 post_as="new_thread")


def record_ask(state, round, discussion_id):
    """Persist a successful ask: the posted thread's discussion id is the
    durable identity replies are matched against. The FIRST recorded id
    wins: later asks are replies on the same thread, so a different id
    passed here never orphans the original thread."""
    validate_state(state)
    _require_round(round)
    recorded = dict(state)
    recorded["screenshot_state"] = STATE_REQUESTED
    if not recorded["discussion_id"]:
        recorded["discussion_id"] = discussion_id
    recorded["asked_round"] = round
    return recorded


def note_has_upload_image(body):
    """True when a note body embeds a GitLab-uploaded image."""
    return bool(IMAGE_UPLOAD_RE.search(body or ""))


def detect_image_reply(recorded_discussion_id, event_discussion_id,
                       note_body):
    """True when a comment event is a reply on the recorded ask thread AND
    its body embeds an uploaded image (the attachment boolean lies)."""
    if not recorded_discussion_id:
        return False
    if event_discussion_id != recorded_discussion_id:
        return False
    return note_has_upload_image(note_body)


def on_image_reply(state):
    """Accept a detected image reply: compliance recorded, requested label
    cleared, one reaction. Nothing is gated on this."""
    validate_state(state)
    if state["screenshot_state"] == STATE_POSTED:
        return _plan("none", "already_posted", state)
    posted = dict(state)
    posted["screenshot_state"] = STATE_POSTED
    return _plan("image_accepted", "image_accepted", posted,
                 labels_remove=(SCREENSHOT_REQUESTED_LABEL,), react=True)


def decide_push_reask(state, round, detection, mr_state="opened",
                      mr_draft=False):
    """Staleness on a push: only a frontend-touching push AFTER an image
    landed re-arms the request - one re-ask per round, on the EXISTING
    thread. Non-frontend pushes never churn labels or state."""
    validate_state(state)
    _require_round(round)
    if mr_state.lower() in TERMINAL_MR_STATES:
        return _plan("none", "mr_terminal", state)
    if mr_draft:
        return _plan("none", "draft_wait", state)
    level = (detection or {}).get("level", "none")
    if level == "none":
        return _plan("none", "not_frontend", state)
    if level == "weak":
        return _plan("none", "weak_frontend_mention_only", state)
    if state["screenshot_state"] == STATE_POSTED:
        if _reasked_this_round(state, round):
            return _plan("none", "reask_capped_this_round", state)
        re_armed = dict(state)
        re_armed["screenshot_state"] = STATE_REQUESTED
        re_armed["reasked_round"] = round
        return _plan("reask", "frontend_push_rearm", re_armed,
                     labels_add=(SCREENSHOT_REQUESTED_LABEL,))
    if state["screenshot_state"] == STATE_REQUESTED:
        return _plan("none", "awaiting_reply", state)
    return _plan("none", "no_ask_outstanding", state)
