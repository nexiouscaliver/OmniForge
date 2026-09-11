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
