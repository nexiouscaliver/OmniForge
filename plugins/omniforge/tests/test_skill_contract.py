"""Contract tests for the omnireview-gitlab SKILL.md + reference docs (W3 T2).

Pins the W3 locked contract: the four engine-brief phrases survive verbatim in
SKILL.md; NO confidence-arithmetic tokens (`+15`, `+25`, `-30`, `−30` — both
minus characters) anywhere in SKILL.md or the consolidation guide; Phase 4
names the validator + consolidator + ONE-pass worklist; every reviewer brief
carries the one-dispatch rule; and the machine-readable findings-block heading
is byte-identical across the three brief templates.
"""

import os
import unittest

SKILL_DIR = os.path.join(os.path.dirname(__file__), "..", "skills",
                         "omnireview-gitlab")

BRIEFS = ("mr-analyst-prompt.md", "codebase-reviewer-prompt.md",
          "security-reviewer-prompt.md")

# Must stay byte-identical across all three reviewer brief templates.
FINDINGS_HEADING = "## Machine-readable findings block (REQUIRED — final block of your report)"

# Numeric literals ONLY (both minus characters) — wording like
# "never adjusted, never recomputed" must not self-violate the test.
ARITHMETIC_TOKENS = ("+15", "+25", "-30", "−30")

ONE_DISPATCH_MARKER = "Do NOT spawn child subagents"


def read(rel):
    with open(os.path.join(SKILL_DIR, rel), encoding="utf-8") as fh:
        return fh.read()


class SkillContractTests(unittest.TestCase):
    def test_four_contract_phrases_present(self):
        t = read("SKILL.md")
        for phrase in ("Phase 5", "Option 1", "Phase 7", "confidence >= 70"):
            self.assertIn(phrase, t)

    def test_guides_contain_no_confidence_arithmetic(self):
        for name in ("consolidation-guide.md", "posting-guide.md"):
            t = read(os.path.join("references", name)).lower()
            for tok in ARITHMETIC_TOKENS:
                self.assertNotIn(tok, t, "%s contains %r" % (name, tok))

    def test_skill_md_contains_no_confidence_arithmetic(self):
        t = read("SKILL.md").lower()
        for tok in ARITHMETIC_TOKENS:
            self.assertNotIn(tok, t)

    def test_phase4_names_validator_and_consolidator(self):
        t = read("SKILL.md")
        self.assertIn("omni_validate_findings.py", t)
        self.assertIn("omni_consolidate.py", t)
        self.assertIn("ONE pass", t)

    def test_phase6_option1_names_posting_fallback_script(self):
        t = read("SKILL.md")
        opt1 = t.index("### Option 1: Full Review Post (Recommended)")
        phase7 = t.index("## Phase 7")
        self.assertLess(opt1, phase7)
        self.assertIn("omni_post_review.py", t[opt1:phase7],
                      "Phase 6 Option 1 must name the shipped posting fallback script")

    def test_one_dispatch_sentence_in_all_briefs(self):
        for name in BRIEFS:
            t = read(os.path.join("references", name))
            self.assertIn(ONE_DISPATCH_MARKER, t,
                          "%s lacks the one-dispatch rule" % name)

    def test_sub60_sleep_one_liner_present(self):
        t = read("SKILL.md")
        self.assertIn("Never sleep-poll — not even sub-60 s sleeps", t)

    def test_phase1_partition_wiring_and_phase3_placeholder(self):
        t = read("SKILL.md")
        phase1 = t.index("## Phase 1")
        phase2 = t.index("## Phase 2")
        self.assertIn("omni_partition.py", t[phase1:phase2],
                      "Phase 1 must name the shipped partition script")
        construction = t.index("### Agent Prompt Construction")
        waiter = t.index("### Wait for Completion")
        self.assertIn("{OWNED_FILES}", t[construction:waiter],
                      "Agent prompt construction must wire the owned-files "
                      "placeholder from the Phase 1 partition")

    def test_phase1_names_partition_and_digest(self):
        t = read("SKILL.md")
        phase1 = t.index("## Phase 1")
        phase2 = t.index("## Phase 2")
        self.assertIn("omni_partition.py", t[phase1:phase2],
                      "Phase 1 must name the shipped partition script")
        self.assertIn("omni_digest.py", t[phase1:phase2],
                      "Phase 1 must name the shipped digest script")
        self.assertIn("/tmp/omni_mr{id}_discussions.json", t[phase1:phase2],
                      "Phase 1 must save the fetch_mr_discussions output")

    def test_phase4_wires_prior_flag_for_retrospective(self):
        t = read("SKILL.md")
        phase4 = t.index("## Phase 4")
        phase5 = t.index("## Phase 5")
        wiring = t[phase4:phase5]
        self.assertIn("--prior", wiring,
                      "Phase 4 retrospective wiring must pass --prior")
        self.assertIn("/tmp/omni_mr{id}_prior_findings.json", wiring)

    def test_findings_heading_byte_identical_across_briefs(self):
        want = FINDINGS_HEADING.encode("utf-8")
        seen = set()
        for name in BRIEFS:
            with open(os.path.join(SKILL_DIR, "references", name), "rb") as fh:
                raw = fh.read()
            headings = [ln for ln in raw.split(b"\n")
                        if ln.startswith(b"## Machine-readable findings block")]
            self.assertEqual(len(headings), 1,
                             "%s must carry the heading exactly once" % name)
            self.assertEqual(headings[0], want, name)
            seen.add(headings[0])
        self.assertEqual(len(seen), 1)      # byte-identical across templates


if __name__ == "__main__":
    unittest.main()
