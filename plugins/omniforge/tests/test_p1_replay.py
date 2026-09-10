"""Corpus replay harness tests (P1) — the three replay assertions over the
persisted audit-corpus fixture subset.

The fixture subset under tests/fixtures/omnicheck-audit/ is a READ-ONLY copy
of the engine repo's claudedocs/omnicheck-audit-2026-09/ (never edit, never
regenerate — the engine original is the evidence of record).

Assertions (from the P1 brief):
 (a) no previously-CORRECT run flips wrong (model outcome == oracle outcome)
 (b) the approve-despite-outstanding runs (cleo !1108/!1172/!1180/!1184/!1226)
     no longer report CLEAN
 (c) the over-rejection cases no longer block: cleo !1306/!1447 and loop
     !59/!60 whole-run, and loop !59/!60/!62 decision/disposed threads never
     appear among a run's blockers (!62 also carries 8 auditor-verified
     unaddressed Important findings — its legitimate blockers stay).
"""

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.abspath(os.path.dirname(__file__))
HARNESS = os.path.join(HERE, "..", "skills", "omnicheck-gitlab", "scripts",
                       "replay_audit.py")
FIXTURE_CORPUS = os.path.join(HERE, "fixtures", "omnicheck-audit")


def run_replay(corpus=FIXTURE_CORPUS, extra=()):
    r = subprocess.run(
        [sys.executable, HARNESS, "--corpus", corpus, "--json", "-", *extra],
        capture_output=True, text=True, timeout=300)
    return r


class ReplayHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proc = run_replay()
        cls.assertEqual(cls.proc.returncode, 0,
                        "harness failed:\n%s\n%s" % (cls.proc.stdout[-3000:],
                                                     cls.proc.stderr[-2000:]))
        # the last stdout line is the JSON payload
        cls.table = cls.proc.stdout.rstrip().splitlines()
        json_start = next(i for i, l in enumerate(cls.table) if l.startswith("{"))
        cls.result = json.loads("\n".join(cls.table[json_start:]))

    def test_harness_ran_every_fixture_run(self):
        ran = self.result["runs"]
        self.assertEqual(len(ran), 17)  # 15 MRs, loop61+cleo1125 multi-run sets included

    def test_a_no_correct_run_flips_wrong(self):
        wrong = [r for r in self.result["assertions"]["a"]["mismatches"]]
        self.assertEqual(wrong, [], "previously-correct runs flipped: %s" % wrong)

    def test_b_approve_despite_outstanding_no_longer_clean(self):
        res = self.result["assertions"]["b"]["cases"]
        for key in ("cleo!1108", "cleo!1172", "cleo!1180", "cleo!1184",
                    "cleo!1226"):
            self.assertIn(key, res, key)
            self.assertNotEqual(res[key]["model_outcome"], "CLEAN", key)
            self.assertNotEqual(res[key]["oracle_outcome"], "CLEAN", key)

    def test_c_over_rejection_no_longer_blocks(self):
        res = self.result["assertions"]["c"]["cases"]
        for key in ("cleo!1306", "cleo!1447", "loop!59", "loop!60"):
            self.assertIn(key, res, key)
            self.assertNotEqual(res[key]["model_outcome"], "BLOCKED", key)

    def test_c_decision_threads_never_among_blockers(self):
        res = self.result["assertions"]["c"]["cases"]
        for key in ("loop!59", "loop!60", "loop!62"):
            self.assertIn(key, res, key)
            disposed_blockers = [
                b for b in res[key]["blockers"]
                if b["disposition"] in ("deferred", "declined", "decision",
                                        "artifact")]
            self.assertEqual(disposed_blockers, [], key)

    def test_loop62_blockers_are_auditor_verified_unaddressed(self):
        # !62 keeps its legitimate blockers: unaddressed Important code/doc
        # findings the auditor verified — the over-rejection fix must not
        # hand-wave real findings away
        res = self.result["assertions"]["c"]["cases"]["loop!62"]
        self.assertTrue(res["blockers"], "!62 lost its legitimate blockers")
        for b in res["blockers"]:
            self.assertEqual(b["disposition"], "not_fixed")
            self.assertIn(b["severity"], ("critical", "important"))

    def test_table_rendered(self):
        header = next(l for l in self.table if l.startswith("|"))
        self.assertIn("repo", header)
        self.assertIn("model", header.lower() or header)
        # every fixture run has a table row
        body = [l for l in self.table if l.startswith("| cleo") or l.startswith("| loop") or l.startswith("| omniforge")]
        self.assertEqual(len(body), len(self.result["runs"]))


if __name__ == "__main__":
    unittest.main()
