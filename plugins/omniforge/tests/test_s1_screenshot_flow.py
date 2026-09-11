"""S1 - frontend detection + label vocabulary for the screenshot ask-author flow.

Unit tests for the PURE logic in tools/omni_ui_screenshot.py:
- classify_frontend_change: extensions-dominant detection; paths are a weak
  tiebreaker only; `app/` is NEVER a signal (the fleet's FastAPI backends
  live under app/ and must not label-churn).
- The label vocabulary: dash-style default (omniforge-ui,
  omniforge-screenshot-requested) avoiding the Premium scoped-label trap
  (all omniforge::* share one scope key -> mutually exclusive on Premium);
  the convention is flippable in ONE place.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from omni_ui_screenshot import (
    ALL_FLOW_LABELS,
    LABEL_CONVENTION,
    SCREENSHOT_REQUESTED_LABEL,
    UI_LABEL,
    classify_frontend_change,
    labels_for_convention,
)


# -- Detection: extensions dominant --------------------------


class TestSDetectionStrong:
    @pytest.mark.parametrize("path", [
        "src/Button.tsx",
        "src/components/App.jsx",
        "ui/views/Login.vue",
        "widgets/Card.svelte",
    ])
    def test_s1_strong_extensions(self, path):
        result = classify_frontend_change([path])
        assert result["level"] == "strong"
        assert result["matched"] == [path]

    def test_s1_strong_wins_over_many_backend_files(self):
        files = [
            "app/main.py",
            "app/api/routes.py",
            "tests/test_server.py",
            "src/Button.tsx",
            "requirements.txt",
        ]
        result = classify_frontend_change(files)
        assert result["level"] == "strong"
        assert result["matched"] == ["src/Button.tsx"]

    def test_s1_uppercase_extension_still_strong(self):
        result = classify_frontend_change(["src/Button.TSX"])
        assert result["level"] == "strong"

    def test_s1_strong_file_not_double_counted_as_weak(self):
        # a .tsx inside components/ is strong only - never also weak-matched
        result = classify_frontend_change(["src/components/Button.tsx"])
        assert result["level"] == "strong"
        assert result["weak_matched"] == []

    def test_s1_result_shape(self):
        result = classify_frontend_change(["src/Button.tsx", "styles.css"])
        assert set(result) == {"level", "matched", "weak_matched"}
        assert result["matched"] == ["src/Button.tsx"]
        assert result["weak_matched"] == ["styles.css"]


class TestSDetectionAppNegative:
    def test_s1_app_path_alone_is_none(self):
        # THE fleet false-positive: FastAPI backends under app/
        result = classify_frontend_change([
            "app/main.py",
            "app/api/users.py",
            "app/models/order.py",
        ])
        assert result["level"] == "none"
        assert result["matched"] == []
        assert result["weak_matched"] == []

    def test_s1_app_pages_not_a_weak_hint(self):
        # "app/pages/..." must not fire the pages/ hint either - app/ confers
        # nothing; only a real frontend path outside app/ does
        result = classify_frontend_change(["app/pages/render.py"])
        assert result["level"] == "none"


class TestSDetectionWeak:
    @pytest.mark.parametrize("path,kind", [
        ("styles/main.css", "ext"),
        ("theme/dark.scss", "ext"),
        ("assets/old.sass", "ext"),
        ("assets/legacy.less", "ext"),
        ("package.json", "file"),
        ("web/package.json", "file"),
        ("src/components/Header.js", "segment"),
        ("src/pages/Home.js", "segment"),
        ("src/stories/Button.stories.js", "segment"),
        ("src/ui/Toolbar.js", "prefix"),
    ])
    def test_s1_weak_signals(self, path, kind):
        result = classify_frontend_change([path])
        assert result["level"] == "weak"
        assert result["matched"] == []
        assert result["weak_matched"] == [path]

    def test_s1_plain_js_outside_hint_paths_is_none(self):
        result = classify_frontend_change(["src/utils/format.js"])
        assert result["level"] == "none"

    def test_s1_empty_and_degenerate_inputs(self):
        assert classify_frontend_change([])["level"] == "none"
        assert classify_frontend_change([""])["level"] == "none"
        assert classify_frontend_change(["README.md", "Makefile"])["level"] == "none"


# -- Label vocabulary -----------------------------------------


class TestSLabels:
    def test_s1_default_convention_is_dash_style(self):
        assert LABEL_CONVENTION == "dash"
        assert UI_LABEL == "omniforge-ui"
        assert SCREENSHOT_REQUESTED_LABEL == "omniforge-screenshot-requested"

    def test_s1_dash_labels_avoid_the_premium_scope_trap(self):
        # Premium scoped labels sharing a key are mutually exclusive -
        # the shipped defaults must not contain a scope separator
        for label in ALL_FLOW_LABELS:
            assert "::" not in label

    def test_s1_scoped_convention_available_for_flip(self):
        ui, requested = labels_for_convention("scoped")
        assert ui == "omniforge::ui"
        assert requested == "omniforge::screenshot-requested"

    def test_s1_dash_convention_values(self):
        ui, requested = labels_for_convention("dash")
        assert ui == "omniforge-ui"
        assert requested == "omniforge-screenshot-requested"

    def test_s1_unknown_convention_rejected(self):
        with pytest.raises(ValueError):
            labels_for_convention("emoji")


# -- Ask-author flow: state machine ---------------------------

from omni_ui_screenshot import (
    STATE_NONE,
    STATE_POSTED,
    STATE_REQUESTED,
    decide_push_reask,
    decide_review_ask,
    detect_image_reply,
    new_state,
    note_has_upload_image,
    on_image_reply,
    record_ask,
    validate_state,
)

STRONG = {"level": "strong", "matched": ["src/App.tsx"], "weak_matched": []}
WEAK = {"level": "weak", "matched": [], "weak_matched": ["styles.css"]}
NOT_FRONTEND = {"level": "none", "matched": [], "weak_matched": []}


class TestSStateBasics:
    def test_s1_new_state_shape(self):
        state = new_state()
        assert state == {
            "screenshot_state": STATE_NONE,
            "discussion_id": "",
            "asked_round": None,
            "reasked_round": None,
        }

    def test_s1_validate_state_rejects_unknown(self):
        with pytest.raises(ValueError):
            validate_state({"screenshot_state": "weird"})
        with pytest.raises(ValueError):
            validate_state({})

    def test_s1_validate_state_accepts_known(self):
        for value in (STATE_NONE, STATE_REQUESTED, STATE_POSTED):
            validate_state({"screenshot_state": value})


class TestSReviewAsk:
    def test_s1_review_ask_first_round(self):
        plan = decide_review_ask(new_state(), 1, STRONG)
        assert plan["action"] == "ask"
        assert plan["labels_add"] == [UI_LABEL, SCREENSHOT_REQUESTED_LABEL]
        assert plan["labels_remove"] == []
        assert plan["react"] is False

    def test_s1_review_ask_draft_waits(self):
        plan = decide_review_ask(new_state(), 1, STRONG, mr_draft=True)
        assert plan["action"] == "none"
        assert plan["reason"] == "draft_wait"
        assert plan["labels_add"] == []

    @pytest.mark.parametrize("mr_state", ["merged", "closed", "locked"])
    def test_s1_review_ask_terminal(self, mr_state):
        plan = decide_review_ask(new_state(), 1, STRONG, mr_state=mr_state)
        assert plan["action"] == "none"
        assert plan["reason"] == "mr_terminal"
        assert plan["labels_add"] == []

    def test_s1_review_ask_non_frontend_no_label_churn(self):
        plan = decide_review_ask(new_state(), 1, NOT_FRONTEND)
        assert plan["action"] == "none"
        assert plan["reason"] == "not_frontend"
        assert plan["labels_add"] == [] and plan["labels_remove"] == []

    def test_s1_review_ask_weak_mention_only(self):
        plan = decide_review_ask(new_state(), 1, WEAK)
        assert plan["action"] == "none"
        assert plan["reason"] == "weak_frontend_mention_only"
        assert plan["labels_add"] == []

    def test_s1_review_ask_once_per_round(self):
        state = record_ask(new_state(), 1, "disc-abc")
        plan = decide_review_ask(state, 1, STRONG)
        assert plan["action"] == "none"
        assert plan["reason"] == "already_asked_this_round"

    def test_s1_review_ask_new_round_allows_new_ask(self):
        state = record_ask(new_state(), 1, "disc-abc")
        plan = decide_review_ask(state, 2, STRONG)
        assert plan["action"] == "ask"

    def test_s1_review_ask_after_posted_none(self):
        state = record_ask(new_state(), 1, "disc-abc")
        state = on_image_reply(state)["state"]
        plan = decide_review_ask(state, 2, STRONG)
        assert plan["action"] == "none"
        assert plan["reason"] == "already_posted"

    def test_s1_review_ask_does_not_mutate_input(self):
        state = new_state()
        snapshot = dict(state)
        decide_review_ask(state, 1, STRONG)
        assert state == snapshot

    def test_s1_review_ask_invalid_state_raises(self):
        with pytest.raises(ValueError):
            decide_review_ask({"screenshot_state": "garbage"}, 1, STRONG)


class TestSRecordAsk:
    def test_s1_record_ask_stores_id_and_round(self):
        state = record_ask(new_state(), 3, "disc-xyz")
        assert state["screenshot_state"] == STATE_REQUESTED
        assert state["discussion_id"] == "disc-xyz"
        assert state["asked_round"] == 3


class TestSImageReply:
    @pytest.mark.parametrize("body,expected", [
        ("![shot](/uploads/abc123/shot.png)", True),
        ("here you go: ![render](https://gitlab.com/g/p/uploads/h/render.png)", True),
        ("two: ![a](/uploads/x/a.png) and ![b](/uploads/y/b.png)", True),
        ("[plain link](/uploads/a/b.png)", False),
        ("![not an upload](/not-uploads/a.png)", False),
        ("no markdown at all", False),
        ("", False),
    ])
    def test_s1_note_has_upload_image(self, body, expected):
        assert note_has_upload_image(body) is expected

    def test_s1_detect_image_reply_matches_only_recorded_thread(self):
        assert detect_image_reply("disc-1", "disc-1", "![s](/uploads/a/s.png)") is True
        assert detect_image_reply("disc-1", "disc-2", "![s](/uploads/a/s.png)") is False
        assert detect_image_reply("", "disc-1", "![s](/uploads/a/s.png)") is False
        assert detect_image_reply("disc-1", "disc-1", "no image here") is False

    def test_s1_on_image_reply_clears_label_reacts(self):
        state = record_ask(new_state(), 1, "disc-1")
        plan = on_image_reply(state)
        assert plan["action"] == "image_accepted"
        assert plan["labels_remove"] == [SCREENSHOT_REQUESTED_LABEL]
        assert plan["labels_add"] == []
        assert plan["react"] is True
        assert plan["state"]["screenshot_state"] == STATE_POSTED
        assert plan["state"]["discussion_id"] == "disc-1"

    def test_s1_on_image_reply_idempotent(self):
        state = record_ask(new_state(), 1, "disc-1")
        posted = on_image_reply(state)["state"]
        plan = on_image_reply(posted)
        assert plan["action"] == "none"
        assert plan["reason"] == "already_posted"
        assert plan["react"] is False

    def test_s1_on_image_reply_does_not_mutate_input(self):
        state = record_ask(new_state(), 1, "disc-1")
        snapshot = dict(state)
        on_image_reply(state)
        assert state == snapshot


class TestSPushReask:
    def _posted_state(self):
        state = record_ask(new_state(), 1, "disc-1")
        return on_image_reply(state)["state"]

    def test_s1_push_reask_after_posted(self):
        state = self._posted_state()
        plan = decide_push_reask(state, 1, STRONG)
        assert plan["action"] == "reask"
        assert plan["labels_add"] == [SCREENSHOT_REQUESTED_LABEL]
        assert plan["labels_remove"] == []
        assert plan["state"]["screenshot_state"] == STATE_REQUESTED
        assert plan["state"]["discussion_id"] == "disc-1"
        assert plan["state"]["reasked_round"] == 1

    def test_s1_push_reask_capped_once_per_round(self):
        state = self._posted_state()
        state["reasked_round"] = 2
        plan = decide_push_reask(state, 2, STRONG)
        assert plan["action"] == "none"
        assert plan["reason"] == "reask_capped_this_round"
        assert plan["state"]["screenshot_state"] == STATE_POSTED

    def test_s1_push_reask_new_round_allows(self):
        state = self._posted_state()
        state["reasked_round"] = 1
        plan = decide_push_reask(state, 2, STRONG)
        assert plan["action"] == "reask"
        assert plan["state"]["reasked_round"] == 2

    def test_s1_push_non_frontend_no_churn(self):
        state = self._posted_state()
        plan = decide_push_reask(state, 1, NOT_FRONTEND)
        assert plan["action"] == "none"
        assert plan["reason"] == "not_frontend"
        assert plan["labels_add"] == []
        assert plan["state"]["screenshot_state"] == STATE_POSTED

    def test_s1_push_weak_no_churn(self):
        state = self._posted_state()
        plan = decide_push_reask(state, 1, WEAK)
        assert plan["action"] == "none"
        assert plan["reason"] == "weak_frontend_mention_only"
        assert plan["state"]["screenshot_state"] == STATE_POSTED

    def test_s1_push_while_requested_none(self):
        state = record_ask(new_state(), 1, "disc-1")
        plan = decide_push_reask(state, 1, STRONG)
        assert plan["action"] == "none"
        assert plan["reason"] == "awaiting_reply"
        assert plan["state"]["screenshot_state"] == STATE_REQUESTED

    def test_s1_push_state_none_no_action(self):
        plan = decide_push_reask(new_state(), 1, STRONG)
        assert plan["action"] == "none"
        assert plan["reason"] == "no_ask_outstanding"

    def test_s1_push_draft_waits(self):
        state = self._posted_state()
        plan = decide_push_reask(state, 1, STRONG, mr_draft=True)
        assert plan["action"] == "none"
        assert plan["reason"] == "draft_wait"
        assert plan["state"]["screenshot_state"] == STATE_POSTED

    @pytest.mark.parametrize("mr_state", ["merged", "closed"])
    def test_s1_push_merged_terminal(self, mr_state):
        state = self._posted_state()
        plan = decide_push_reask(state, 1, STRONG, mr_state=mr_state)
        assert plan["action"] == "none"
        assert plan["reason"] == "mr_terminal"
        assert plan["state"]["screenshot_state"] == STATE_POSTED

    def test_s1_push_reask_does_not_mutate_input(self):
        state = self._posted_state()
        snapshot = dict(state)
        decide_push_reask(state, 1, STRONG)
        assert state == snapshot
