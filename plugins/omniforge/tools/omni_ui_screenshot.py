"""Pure logic for the S1 frontend-screenshot ask-the-author flow (WP-S1).

STUB — RED state for TDD; implementation lands in the GREEN commit.
"""

ALL_FLOW_LABELS = ()
LABEL_CONVENTION = ""
SCREENSHOT_REQUESTED_LABEL = ""
UI_LABEL = ""


def classify_frontend_change(changed_files):
    raise NotImplementedError


def labels_for_convention(convention):
    raise NotImplementedError
