"""SC-9 wrap tests: the fix brief ships as one copyable fenced markdown block.

AC-9.1 — wrap_brief_md_block fence sizing: a plain payload gets the
4-backtick minimum fence; a payload containing a 4-backtick run grows the
fence to 5; a 1-backtick payload (today's composed-brief shape —
_sanitize_body collapses every untrusted backtick run, template code spans
are single backticks) stays 4.
AC-9.2 — the payload contract is enforced BEFORE any wrapping: a text not
ending in exactly one newline ("" and "a\n\n") raises AssertionError.
AC-9.3 — the rendered golden brief: exactly two backtick runs of length >=3
(the outer fences), equal and >=4, first line == fence + "markdown", last
content line == the closing fence, and the payload between them
BYTE-IDENTICAL to the pre-change composed brief (the unwrapped GOLDEN_BRIEF
byte-golden captured in test_omni_fixprompt.py before the wrap landed) —
no leading blank line inside the fence, no trailing blank line before the
closing fence (D14: no extra newline is ever inserted).

Stdlib only — no pytest import — so the module runs identically under bare
unittest and pytest.
"""

import re
import unittest

from tests.test_omni_fixprompt import (GOLDEN_BRIEF, GOLDEN_FINDINGS,
                                       GOLDEN_MAP, GOLDEN_META,
                                       load_fixprompt_module)


class WrapBriefMdBlockTests(unittest.TestCase):
    """AC-9.1 / AC-9.2 — the wrap helper unit contract."""

    def setUp(self):
        self.mod = load_fixprompt_module()

    def test_plain_payload_gets_minimum_four_backtick_fence(self):
        self.assertEqual(self.mod.wrap_brief_md_block("abc\n"),
                         "````markdown\nabc\n````")

    def test_four_backtick_run_grows_fence_to_five(self):
        self.assertEqual(self.mod.wrap_brief_md_block("has ```` run\n"),
                         "`````markdown\nhas ```` run\n`````")

    def test_single_backtick_payload_stays_four(self):
        self.assertEqual(self.mod.wrap_brief_md_block("code `span` here\n"),
                         "````markdown\ncode `span` here\n````")

    def test_empty_payload_raises_assertion(self):
        with self.assertRaises(AssertionError):
            self.mod.wrap_brief_md_block("")

    def test_double_trailing_newline_raises_assertion(self):
        with self.assertRaises(AssertionError):
            self.mod.wrap_brief_md_block("a\n\n")

    def test_missing_trailing_newline_raises_assertion(self):
        with self.assertRaises(AssertionError):
            self.mod.wrap_brief_md_block("abc")


class RenderedBriefWrapTests(unittest.TestCase):
    """AC-9.3 — the rendered brief is exactly the wrap of the pre-change
    composed payload (byte identity via the unwrapped golden)."""

    def setUp(self):
        self.mod = load_fixprompt_module()

    def test_golden_render_fence_shape_and_payload_identity(self):
        out = self.mod.render_brief(GOLDEN_FINDINGS, GOLDEN_MAP,
                                    GOLDEN_META, "136")
        # the ONLY backtick runs of length >=3 are the two outer fences:
        # equal runs, >=4, first line = fence + "markdown", last content
        # line = the bare closing fence
        runs = re.findall(r"`{3,}", out)
        self.assertEqual(len(runs), 2, out)
        self.assertEqual(runs[0], runs[1])
        self.assertGreaterEqual(len(runs[0]), 4)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(lines[0], runs[0] + "markdown")
        self.assertEqual(lines[-1], runs[0])
        self.assertTrue(out.endswith(runs[0]))       # output ends AT the fence
        # payload identity: strip exactly the fence + "markdown\n" prefix
        # and the closing fence — what remains must be the pre-change
        # composed brief byte for byte (no leading blank line inside the
        # fence, no trailing blank line before the closing fence)
        fence = runs[0]
        payload = out[len(fence) + len("markdown\n"):-len(fence)]
        self.assertEqual(payload, GOLDEN_BRIEF)
        self.assertTrue(payload.startswith("## OmniForge fix brief"))
        self.assertTrue(payload.endswith("\n"))       # exactly one trailing
        self.assertFalse(payload.endswith("\n\n"))


if __name__ == "__main__":
    unittest.main()
