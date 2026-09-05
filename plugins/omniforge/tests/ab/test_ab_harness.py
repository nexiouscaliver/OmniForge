"""A/B harness tests for the R2-D partition change (spec section 8, plan 1.3).

SyntheticGeneratorTests pin T1's generator determinism (they pass from T1
green onward by design — they are shape controls, not this task's red).
AbMetricsTests pin the T3 harness: same-currency old-side recompute, the
emitted-weights crosscheck, the cap/floor boolean primitives, and the
committed ab_result document's schema and PASS record.
"""

import importlib.util
import json
import os
import unittest

AB = os.path.abspath(os.path.join(os.path.dirname(__file__)))
PARTITION = os.path.join(AB, "..", "..", "skills", "omnireview-gitlab",
                         "scripts", "omni_partition.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SyntheticGeneratorTests(unittest.TestCase):
    def test_synthetic_generator_deterministic_both_profiles(self):
        gen = _load(os.path.join(AB, "make_canary_synthetic.py"), "gen")
        part = _load(PARTITION, "part_shapes")
        for profile, seed, counts in (
                ("canary", 1402, (110, 109, 9)),
                ("security-light", 1403, (80, 40, 2))):
            with self.subTest(profile=profile):
                one, two = gen.build_gather(profile), gen.build_gather(profile)
                self.assertEqual(json.dumps(one), json.dumps(two))
                self.assertEqual(gen.PROFILE_SEEDS[profile], seed)
                data = one["data"]
                code = [p for p in data["files_changed"]
                        if not part.is_security(p) and not part.is_test(p)]
                tests = [p for p in data["files_changed"]
                        if part.is_test(p)]
                sec = [p for p in data["files_changed"]
                       if part.is_security(p)]
                self.assertEqual(
                    (len(code), len(tests), len(sec)), counts)
                if profile == "security-light":
                    for p in sec:
                        self.assertLessEqual(
                            len(data["diff_line_map"][p]["added_lines"]), 5)

    def test_committed_fixtures_match_generator(self):
        gen = _load(os.path.join(AB, "make_canary_synthetic.py"), "gen2")
        for profile, name in (("canary", "canary_synth_gather.json"),
                              ("security-light",
                               "security_light_gather.json")):
            with self.subTest(profile=profile):
                with open(os.path.join(AB, name), encoding="utf-8") as fh:
                    committed = fh.read()
                fresh = json.dumps(gen.build_gather(profile), indent=2,
                                   ensure_ascii=False) + "\n"
                self.assertEqual(committed, fresh,
                                 "committed fixture drifted from generator")


class AbMetricsTests(unittest.TestCase):
    @staticmethod
    def _small_old_partition():
        # OLD-style schema (NO weight fields): a.py 10, tests/test_b.py 10,
        # src/auth_c.py 10 -> ownership a.py+tests codebase/analyst, auth security
        return {"files": [
                    {"path": "a.py", "added_lines": 10,
                     "owner": "codebase", "reason": "greedy-balance"},
                    {"path": "tests/test_b.py", "added_lines": 10,
                     "owner": "analyst", "reason": "greedy-balance"},
                    {"path": "src/auth_c.py", "added_lines": 10,
                     "owner": "security", "reason": "security-affinity"}],
                "agents": {"analyst": {"files": ["tests/test_b.py"],
                                       "added_lines_total": 10},
                           "codebase": {"files": ["a.py"],
                                        "added_lines_total": 10},
                           "security": {"files": ["src/auth_c.py"],
                                        "added_lines_total": 10}}}

    def test_side_metrics_old_partition_recomputed(self):
        m = _load(os.path.join(AB, "ab_metrics.py"), "abm")
        part = _load(PARTITION, "part_w")
        ev = m.evaluate(self._small_old_partition(), part.weight,
                        part.is_security)
        # weights: a.py 100 000; test_b 10*100*50=50 000; auth_c 10*1000*100
        self.assertEqual(ev["agents"]["codebase"]["weight_total"], 100000)
        self.assertEqual(ev["agents"]["analyst"]["weight_total"], 50000)
        self.assertEqual(ev["agents"]["security"]["weight_total"], 1000000)
        self.assertTrue(ev["security_invariant"])
        # security pre-load alone over cap -> strict cap False, bound True
        self.assertFalse(ev["agents"]["security"]["cap_strict"])
        # oversize itemization names the file that busts the strict cap
        self.assertEqual(ev["oversize_files"], ["src/auth_c.py"])
        self.assertTrue(ev["pass"])     # +W_max bound + invariant hold

    def test_side_metrics_new_emitted_weights_crosscheck(self):
        m = _load(os.path.join(AB, "ab_metrics.py"), "abm2")
        part = _load(PARTITION, "part_w2")
        gather = json.load(open(os.path.join(
            AB, "canary_synth_gather.json"), encoding="utf-8"))
        new_part = part.partition(gather["data"])
        ev = m.evaluate(new_part, part.weight, part.is_security)
        for a, info in new_part["agents"].items():
            self.assertEqual(ev["agents"][a]["weight_total"],
                             info["weight_total"], a)
        self.assertTrue(ev["cap_pass"] and ev["floor_pass"]
                        and ev["security_invariant"])

    def test_evaluate_cap_floor_booleans(self):
        m = _load(os.path.join(AB, "ab_metrics.py"), "abm3")
        self.assertEqual(m.permille(1000, 3000), 1000)
        self.assertTrue(m.cap_strict(1250, 3000))    # 12*1250 == 5*3000
        self.assertFalse(m.cap_strict(1251, 3000))
        self.assertTrue(m.floor_ok(500, 3000))       # 6*500 == 3000
        self.assertFalse(m.floor_ok(499, 3000))

    def test_ab_result_json_pass_and_schema(self):
        with open(os.path.join(AB, "ab_result.json"),
                  encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["schema"], "omniforge-ab-partition/1")
        self.assertEqual(sorted(doc["corpora"]),
                         ["canary-synth", "real-or-synth",
                          "security-light"])
        self.assertTrue(doc["overall_pass"])
        self.assertIn("old-side weights recomputed", "".join(doc["notes"]))
        real = doc["corpora"]["real-or-synth"]
        if "degenerate_with" in real:
            self.assertEqual(real["degenerate_with"], "canary-synth")
        else:
            self.assertTrue(real["new_pass"])
        for corpus in ("canary-synth", "security-light"):
            entry = doc["corpora"][corpus]
            self.assertTrue(entry["new_pass"], corpus)
            self.assertIn("old", entry)      # baseline reported, no authority


if __name__ == "__main__":
    unittest.main()
