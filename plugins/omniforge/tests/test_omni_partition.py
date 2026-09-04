"""Tests for omni_partition.py (W3 T4) — deterministic diff-based load balancing.

Pinned semantics (spec D7 / plan §T4): security-affinity files are owned by the
security agent FIRST regardless of size (security never receives greedy
overflow); docs/config files prefer the analyst (analyst wins exact load
ties); every remaining file goes greedy largest-first (ties: path asc) into
the least-loaded of {codebase, analyst} only (codebase wins exact ties). Every
changed file gets exactly ONE owner; the partition is a pure function of the
input JSON — same input, byte-identical output. Every agent still SEES every
changed file (cross_cutting_files). Also pins the T4 brief tightening in the
three reviewer templates (owned-files deep dive, blame-once, output caps).

The script is exercised BOTH as a subprocess (exit codes + the one-JSON-line
stdout contract) and in-process via importlib (pure-function determinism);
same conventions as test_omni_validate_findings.py.
"""

import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
    "skills", "omnireview-gitlab", "scripts", "omni_partition.py"))

SKILL_DIR = os.path.join(os.path.dirname(__file__), "..", "skills",
                         "omnireview-gitlab")

BRIEFS = ("mr-analyst-prompt.md", "codebase-reviewer-prompt.md",
          "security-reviewer-prompt.md")

AGENTS = ("analyst", "codebase", "security")


def load_partition_module():
    """In-process loader for the pure partition function."""
    spec = importlib.util.spec_from_file_location("omni_partition_under_test",
                                                  SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tmp_dir(testcase):
    d = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


def mr(files):
    """Build a minimal fetch_mr_data-shaped dict: path -> added-line count.

    diff_line_map entries carry added_lines as a LIST of line numbers (the
    real tool's shape); a couple of tests exercise the bare-int form too.
    """
    return {
        "files_changed": list(files),
        "diff_line_map": {p: {"added_lines": list(range(1, n + 1))}
                          for p, n in files.items()},
    }


def run_script(mr_json, out, timeout=30):
    return subprocess.run(
        [sys.executable, SCRIPT, "--mr-json", mr_json, "--out", out],
        capture_output=True, text=True, timeout=timeout)


def write_json(d, name, obj):
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TestOmniPartition(unittest.TestCase):
    def test_security_affinity_first(self):
        mod = load_partition_module()
        # auth.py is 1 line next to a 500-line doc — affinity wins regardless
        # of size; pipeline + SQL files match too; plain code/docs do not.
        data = mr({"src/auth.py": 1, "docs/README.md": 500,
                   "migrations/001_init.sql": 3, ".gitlab-ci.yml": 5,
                   "src/big.py": 400})
        result = mod.partition(data)
        by_path = {f["path"]: f for f in result["files"]}
        for path in ("src/auth.py", "migrations/001_init.sql",
                     ".gitlab-ci.yml"):
            self.assertEqual(by_path[path]["owner"], "security", path)
            self.assertEqual(by_path[path]["reason"], "security-affinity", path)
        # R2-D: security MAY receive cap-governed greedy spill; any
        # non-affinity file it owns carries reason greedy-balance and the
        # agent stays inside the indivisibility bound (spec section 4.2)
        total = sum(result["agents"][a]["weight_total"] for a in AGENTS)
        wmax = max(mod.weight(f["path"], f["added_lines"])
                   for f in result["files"])
        for path in result["agents"]["security"]["files"]:
            if path not in ("src/auth.py", "migrations/001_init.sql",
                            ".gitlab-ci.yml"):
                self.assertEqual(by_path[path]["reason"],
                                 "greedy-balance", path)
        self.assertLessEqual(
            12 * result["agents"]["security"]["weight_total"],
            5 * total + 12 * wmax)
        for path in ("docs/README.md", "src/big.py"):
            self.assertNotEqual(by_path[path]["owner"], "security", path)
            self.assertNotEqual(by_path[path]["reason"], "security-affinity",
                                path)

    def test_greedy_size_balance(self):
        mod = load_partition_module()
        # 2x-skew fixture sized like the 320-1290s wall case; no affinity files
        data = mr({"src/alpha.py": 400, "src/beta.py": 390,
                   "src/gamma.py": 20, "src/delta.py": 10})
        result = mod.partition(data)
        # R2-D: security is a third greedy bin — totals read over ALL THREE
        # agents (gamma+delta spill to security; security total = 30)
        totals = {a: result["agents"][a]["added_lines_total"]
                  for a in ("analyst", "codebase", "security")}
        largest = 400
        self.assertLessEqual(max(totals.values()) - min(totals.values()),
                             largest,
                         "greedy guarantee violated: %r" % totals)
        self.assertEqual(sum(totals.values()), 820)   # every line counted once
        for f in result["files"]:
            self.assertEqual(f["reason"], "greedy-balance", f)
        # 3-bin indivisibility bound in weight space (spec section 4.2)
        wtotal = sum(result["agents"][a]["weight_total"] for a in AGENTS)
        wmax = max(mod.weight(f["path"], f["added_lines"])
                   for f in result["files"])
        for a in AGENTS:
            self.assertLessEqual(
                12 * result["agents"][a]["weight_total"],
                5 * wtotal + 12 * wmax, a)

    def test_deterministic_pure_function(self):
        mod = load_partition_module()
        data = mr({"src/auth.py": 30, "src/big.py": 400, "README.md": 60,
                   "docs/guide.md": 45, "src/small.py": 5, "cfg/config.json": 12})
        first = json.dumps(mod.partition(data), sort_keys=False,
                           indent=2, ensure_ascii=False)
        second = json.dumps(mod.partition(data), sort_keys=False,
                            indent=2, ensure_ascii=False)
        self.assertEqual(first, second)
        # CLI-level determinism: two subprocess runs -> byte-identical output
        d = tmp_dir(self)
        src = write_json(d, "mr.json", data)
        out1, out2 = os.path.join(d, "p1.json"), os.path.join(d, "p2.json")
        p1 = run_script(src, out1)
        p2 = run_script(src, out2)
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertEqual(p2.returncode, 0, p2.stderr)
        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())
        self.assertEqual(p1.stdout, p2.stdout)

    def test_every_file_owned_exactly_once(self):
        mod = load_partition_module()
        cases = {
            "mixed": mr({"src/auth.py": 20, "src/big.py": 300,
                         "README.md": 40, "src/util.py": 55}),
            "all-security": mr({"src/login_handler.py": 80,
                                "db/schema.sql": 25, ".env.example": 6}),
            "docs-only": mr({"README.md": 40, "NOTES.txt": 10,
                             "LICENSE": 5}),
            "empty": {"files_changed": [], "diff_line_map": {}},
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                result = mod.partition(data)
                changed = data["files_changed"]
                owned = [p for a in AGENTS
                         for p in result["agents"][a]["files"]]
                self.assertEqual(sorted(owned), sorted(changed))
                for p in changed:                    # exactly once across owners
                    self.assertEqual(owned.count(p), 1, p)
                self.assertEqual(len(result["files"]), len(changed))
                for f in result["files"]:            # per-file owner is valid
                    self.assertIn(f["owner"], AGENTS)
                    self.assertIn(f["reason"],
                                 ("security-affinity", "greedy-balance",
                                  "docs-prefer-analyst"))

    def test_docs_prefer_analyst(self):
        mod = load_partition_module()
        # exact load tie: analyst wins the docs file
        data = mr({"src/core.py": 120, "NOTES.md": 120})
        result = mod.partition(data)
        by_path = {f["path"]: f for f in result["files"]}
        self.assertEqual(by_path["NOTES.md"]["owner"], "analyst")
        self.assertEqual(by_path["NOTES.md"]["reason"], "docs-prefer-analyst")
        self.assertEqual(by_path["src/core.py"]["owner"], "codebase")
        # LICENSE (basename match) prefers analyst too
        data2 = mr({"LICENSE": 40, "src/x.py": 40})
        result2 = mod.partition(data2)
        by_path2 = {f["path"]: f for f in result2["files"]}
        self.assertEqual(by_path2["LICENSE"]["owner"], "analyst")
        self.assertEqual(by_path2["LICENSE"]["reason"], "docs-prefer-analyst")

    def test_cross_cutting_visibility_all_agents(self):
        mod = load_partition_module()
        data = mr({"src/auth.py": 20, "src/big.py": 300, "README.md": 40})
        result = mod.partition(data)
        changed = data["files_changed"]
        expected_order = [f["path"] for f in result["files"]]
        for a in AGENTS:
            # every agent sees every changed file, in the canonical
            # (largest-first, path-asc) emission order
            self.assertEqual(result["agents"][a]["cross_cutting_files"],
                             expected_order, a)
            self.assertEqual(sorted(result["agents"][a]["cross_cutting_files"]),
                             sorted(changed), a)
        # totals are consistent: each agent's total = sum of its files' sizes
        sizes = {f["path"]: f["added_lines"] for f in result["files"]}
        for a in AGENTS:
            expected = sum(sizes[p] for p in result["agents"][a]["files"])
            self.assertEqual(result["agents"][a]["added_lines_total"],
                             expected, a)

    def test_no_substring_affinity_false_positives(self):
        mod = load_partition_module()
        # substring hits that are NOT security: tokens must match path
        # SEGMENTS — "auth" must not fire on author/AUTHORS, "token" not on
        # tokenizer
        data = mr({"docs/AUTHORS.md": 120, "src/tokenizer.py": 80,
                   "src/author.py": 60})
        result = mod.partition(data)
        by_path = {f["path"]: f for f in result["files"]}
        for path in ("docs/AUTHORS.md", "src/tokenizer.py", "src/author.py"):
            self.assertNotEqual(by_path[path]["owner"], "security", path)
            self.assertNotEqual(by_path[path]["reason"],
                                "security-affinity", path)
        # underscore-compound names still hit the token list by segment
        data2 = mr({"src/session_store.py": 40, "src/auth_middleware.py": 30})
        result2 = mod.partition(data2)
        for f in result2["files"]:
            self.assertEqual(f["owner"], "security", f["path"])
            self.assertEqual(f["reason"], "security-affinity", f["path"])

    def test_input_order_permutation_stability(self):
        mod = load_partition_module()
        sizes = {"src/auth.py": 30, "src/big.py": 400, "README.md": 60,
                 "docs/guide.md": 45, "src/small.py": 5, "cfg/config.json": 12}
        paths = list(sizes)
        orders = [paths, list(reversed(paths)),
                  ["src/big.py", "README.md", "src/auth.py",
                   "cfg/config.json", "src/small.py", "docs/guide.md"]]
        rng = random.Random(42)
        for _ in range(5):
            shuffled = list(paths)
            rng.shuffle(shuffled)
            orders.append(shuffled)
        reference = None
        ref_owners = None
        for order in orders:
            data = {"files_changed": order,
                    "diff_line_map": {p: {"added_lines": list(
                        range(1, sizes[p] + 1))} for p in order}}
            result = mod.partition(data)
            dumped = json.dumps(result, sort_keys=False, indent=2,
                                ensure_ascii=False)
            owners = {f["path"]: (f["owner"], f["reason"])
                      for f in result["files"]}
            if reference is None:
                reference, ref_owners = dumped, owners
            else:
                self.assertEqual(dumped, reference,
                                 "output bytes depend on input list order")
                self.assertEqual(owners, ref_owners)

    def test_equal_size_tie_is_deterministic(self):
        mod = load_partition_module()
        result = mod.partition(mr({"a.py": 10, "b.py": 10}))
        by_path = {f["path"]: f for f in result["files"]}
        # both start unloaded: a.py sorts first (path asc) and codebase wins
        # the exact tie; b.py then flows to the less-loaded analyst
        self.assertEqual(by_path["a.py"]["owner"], "codebase")
        self.assertEqual(by_path["b.py"]["owner"], "analyst")
        # reversed input order must not flip the pinned assignment
        result2 = mod.partition({
            "files_changed": ["b.py", "a.py"],
            "diff_line_map": {"a.py": {"added_lines": list(range(1, 11))},
                              "b.py": {"added_lines": list(range(1, 11))}}})
        by_path2 = {f["path"]: f for f in result2["files"]}
        self.assertEqual(by_path2["a.py"]["owner"], "codebase")
        self.assertEqual(by_path2["b.py"]["owner"], "analyst")


class PartitionCliAndBriefContractTests(unittest.TestCase):
    """CLI contract (stdout line, usage exit 2) + the T4 brief tightening."""

    def test_cli_stdout_line_and_usage_exit2(self):
        d = tmp_dir(self)
        src = write_json(d, "mr.json",
                         mr({"src/auth.py": 20, "src/big.py": 300}))
        out = os.path.join(d, "partition.json")
        proc = run_script(src, out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, "stdout must be exactly one JSON line")
        st = json.loads(lines[0])
        # auth.py -> security (affinity); big.py -> codebase (greedy tie ->
        # codebase); analyst owns nothing in this fixture
        self.assertEqual(st, {"files": 2,
                              "agents": {"analyst": 0, "codebase": 1,
                                         "security": 1}})
        # written output matches the documented schema
        data = load_json(out)
        self.assertEqual(sorted(data), ["agents", "files"])
        self.assertEqual(sorted(data["agents"]), sorted(AGENTS))
        # usage error: missing required flags -> argparse exit 2
        bad = subprocess.run([sys.executable, SCRIPT], capture_output=True,
                             text=True, timeout=30)
        self.assertEqual(bad.returncode, 2)
        # unreadable input file -> exit 2 with a stderr diagnostic
        bad2 = run_script(os.path.join(d, "missing.json"), out)
        self.assertEqual(bad2.returncode, 2)
        self.assertIn("omni_partition", bad2.stderr)

    def test_briefs_carry_tightening_contract(self):
        for name in BRIEFS:
            with open(os.path.join(SKILL_DIR, "references", name),
                      encoding="utf-8") as fh:
                t = fh.read()
            for fragment in (
                "owned files",                              # deep-dive ownership
                "at most once per finding locus",           # blame-once cap
                "do not re-read",                           # no repeated re-reads
                "≤ 25 words",                               # output volume caps
            ):
                self.assertIn(fragment, t,
                              "%s lacks tightening fragment %r" % (name,
                                                                   fragment))
            # the ownership table is injected via the placeholder
            self.assertIn("{OWNED_FILES}", t, name)


class GatherFileInputTests(unittest.TestCase):
    """W4 T1 shim: --mr-json accepts the omniforge-mr-gather/1 file by
    operating on its embedded data envelope; legacy single-envelope inputs
    are byte-identical to 3.3.0."""

    @staticmethod
    def gather_file(envelope):
        return {
            "schema": "omniforge-mr-gather/1",
            "fetched_at": "2026-09-03T00:00:00Z",
            "project": "73279395",
            "mr_iid": "21",
            "diff_refs": {"base_sha": "b", "head_sha": "h", "start_sha": "s"},
            "data": envelope,
            "discussions": {"success": True, "mr_id": "21",
                            "discussions": [], "total": 0, "unresolved": 0,
                            "resolved": 0},
            "versions": [],
        }

    def test_partition_accepts_gather_file(self):
        d = tmp_dir(self)
        envelope = mr({"src/auth.py": 20, "src/big.py": 300})
        gather = write_json(d, "gather.json", self.gather_file(envelope))
        plain = write_json(d, "data.json", envelope)
        out1 = os.path.join(d, "from_gather.json")
        out2 = os.path.join(d, "from_data.json")
        p1 = run_script(gather, out1)
        p2 = run_script(plain, out2)
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertEqual(p2.returncode, 0, p2.stderr)
        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read(),
                             "gather file must partition as its embedded data")
        self.assertEqual(p1.stdout, p2.stdout)

    def test_partition_legacy_mr_json_unchanged(self):
        d = tmp_dir(self)
        envelope = mr({"src/auth.py": 20, "src/big.py": 300, "README.md": 40})
        src = write_json(d, "mr.json", envelope)
        out1 = os.path.join(d, "p1.json")
        out2 = os.path.join(d, "p2.json")
        p1 = run_script(src, out1)
        p2 = run_script(src, out2)
        self.assertEqual(p1.returncode, 0, p1.stderr)
        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())   # deterministic
        # and identical to the in-process pure function's dump (3.3.0 shape)
        mod = load_partition_module()
        expected = json.dumps(mod.partition(envelope), indent=2,
                              ensure_ascii=False) + "\n"
        with open(out1, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), expected)
        # a top-level "data" key ALONE (no discussions) must NOT unwrap
        quirky = dict(envelope)
        quirky["data"] = {"files_changed": [], "diff_line_map": {}}
        src2 = write_json(d, "quirky.json", quirky)
        out3 = os.path.join(d, "p3.json")
        p3 = run_script(src2, out3)
        self.assertEqual(p3.returncode, 0, p3.stderr)
        with open(out3, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), expected,
                             "detection requires BOTH data and discussions")


CANARY_AB = os.path.join(os.path.dirname(__file__), "ab",
                         "canary_synth_gather.json")


def load_ab_gather(name):
    with open(os.path.join(os.path.dirname(__file__), "ab", name),
              encoding="utf-8") as fh:
        return json.load(fh)


class WeightModelTests(unittest.TestCase):
    """R2-D SC-1: the per-file cost model (spec section 4.1)."""

    def test_weight_formula_factors(self):
        mod = load_partition_module()
        self.assertEqual((mod.W_LANG_DEFAULT, mod.W_LANG_SECURITY,
                          mod.W_TEST_DEFAULT, mod.W_TEST_FILE),
                         (100, 1000, 100, 50))
        self.assertEqual(mod.weight("src/app.py", 10), 10 * 100 * 100)
        self.assertEqual(mod.weight("docs/guide.md", 10), 10 * 100 * 100)
        self.assertEqual(mod.weight("src/auth.py", 10), 10 * 1000 * 100)
        self.assertEqual(mod.weight("tests/test_app.py", 10), 10 * 100 * 50)
        self.assertEqual(mod.weight("tests/test_auth.py", 10), 10 * 1000 * 50)
        self.assertEqual(mod.weight("legacy.py", 0), 1 * 100 * 100)   # floor
        self.assertEqual(mod.weight("src/auth.py", 0), 1 * 1000 * 100)

    def test_is_test_classifier_segments(self):
        mod = load_partition_module()
        for yes in ("tests/test_x.py", "src/__tests__/y.ts", "test_foo.py",
                    "x_test.py", "x_test.go", "x_test.js", "x_test.rb",
                    "x.spec.js", "x.spec.ts", "x_spec.rb"):
            self.assertTrue(mod.is_test(yes), yes)
        for no in ("latest.py", "contest.py", "attest.py",
                   "docs/testing.md", "src/contest.js"):
            self.assertFalse(mod.is_test(no), no)

    def test_weight_integer_no_floats(self):
        mod = load_partition_module()
        result = mod.partition(mr({"src/auth.py": 20, "src/big.py": 300,
                                   "README.md": 40, "tests/test_x.py": 7,
                                   "legacy.py": 0}))
        for f in result["files"]:
            self.assertIsInstance(f["weight"], int, f)
        for a in AGENTS:
            self.assertIsInstance(result["agents"][a]["weight_total"], int, a)

        def _no_floats(o):
            if isinstance(o, float):
                return False
            if isinstance(o, dict):
                return all(_no_floats(v) for v in o.values())
            if isinstance(o, list):
                return all(_no_floats(v) for v in o)
            return True
        self.assertTrue(_no_floats(result))


class WeightBalanceTests(unittest.TestCase):
    """R2-D cap/floor/spill outcomes (spec section 7.1) on the committed
    synthetic canary fixture (section 8.1 corpus (ii) — deterministic shape
    control, always run)."""

    def _canary(self):
        mod = load_partition_module()
        gather = load_ab_gather("canary_synth_gather.json")
        return mod, gather["data"], mod.partition(gather["data"])

    def test_canary_shape_228_cap_and_floor(self):
        mod, data, result = self._canary()
        self.assertEqual(len(data["files_changed"]), 228)
        aff = [p for p in data["files_changed"] if mod.is_security(p)]
        self.assertEqual(len(aff), 9)
        total = sum(result["agents"][a]["weight_total"] for a in AGENTS)
        for a in AGENTS:
            wt = result["agents"][a]["weight_total"]
            self.assertLessEqual(12 * wt, 5 * total,      # (a) strict cap
                                 "%s over cap: %d" % (a, wt))
            self.assertGreaterEqual(6 * wt, total,        # (b) floor
                                    "%s starved: %d" % (a, wt))
        by_path = {f["path"]: f for f in result["files"]}
        for p in aff:                                     # (c) invariant
            self.assertEqual(by_path[p]["owner"], "security", p)
            self.assertEqual(by_path[p]["reason"], "security-affinity", p)

    def test_canary_shape_third_agent_receives_scope(self):
        mod, data, result = self._canary()
        aff = [p for p in data["files_changed"] if mod.is_security(p)]
        total = sum(result["agents"][a]["weight_total"] for a in AGENTS)
        aff_w = sum(mod.weight(p, mod.added_lines(data, p)) for p in aff)
        sec_files = result["agents"]["security"]["files"]
        by_path = {f["path"]: f for f in result["files"]}
        # security always owns AT LEAST its affinity set (section 3.4 invariant);
        # with affinity in [0.5x, 1.0x) ideal it sits above the floor but below
        # ideal, so greedy ALSO spills filler to it (it is a full greedy bin) —
        # exact equality with the affinity set holds ONLY at affinity >= ideal.
        self.assertGreaterEqual(set(sec_files), set(aff))
        for p in sec_files:
            if p not in set(aff):
                self.assertEqual(by_path[p]["reason"],
                                 "greedy-balance", p)
        if 6 * aff_w < total:            # affinity alone under the floor
            self.assertGreater(len(sec_files), len(aff),
                               "security starved but got no spill")
        ideal = total // 3
        print("canary: affinity weight %d / ideal %d / security owns %d files"
              % (aff_w, ideal, len(sec_files)))

    def test_cap_enforcement_excludes_full_bins(self):
        mod = load_partition_module()
        result = mod.partition(mr({"a%02d.py" % i: 10 for i in range(6)}))
        counts = {a: len(result["agents"][a]["files"]) for a in AGENTS}
        self.assertEqual(sorted(counts.values()), [2, 2, 2])
        total = sum(result["agents"][a]["weight_total"] for a in AGENTS)
        for a in AGENTS:
            self.assertLessEqual(12 * result["agents"][a]["weight_total"],
                                 5 * total, a)
        # lopsided: the 400-line bin lands over cap and receives NOTHING more
        files = {"src/big.py": 400}
        files.update({"s%02d.py" % i: 10 for i in range(9)})
        result2 = mod.partition(mr(files))
        self.assertEqual(result2["agents"]["codebase"]["files"],
                         ["src/big.py"])
        total2 = sum(result2["agents"][a]["weight_total"] for a in AGENTS)
        wmax = max(mod.weight(p, n) for p, n in files.items())
        for a in AGENTS:
            self.assertLessEqual(12 * result2["agents"][a]["weight_total"],
                                 5 * total2 + 12 * wmax, a)  # +W_max bound

    def test_affinity_overload_still_exit0(self):
        d = tmp_dir(self)
        src = write_json(d, "mr.json", mr({"src/auth_a.py": 200,
                                           "src/auth_b.py": 200,
                                           "src/auth_c.py": 200,
                                           "src/auth_d.py": 200}))
        out = os.path.join(d, "p.json")
        proc = run_script(src, out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = load_json(out)
        for f in result["files"]:        # no redress, no failure (section 4.2)
            self.assertEqual(f["owner"], "security", f)
            self.assertEqual(f["reason"], "security-affinity", f)
            self.assertIn("weight", f)
        self.assertIn("weight_total", result["agents"]["security"])

    def test_floor_and_cap_remainder_bound(self):
        mod = load_partition_module()
        rng = random.Random(20260905)
        cases = [{"files_changed": [], "diff_line_map": {}},
                 mr({"only.py": 50}),
                 mr({"a.py": 10, "b.py": 10}),
                 mr({"README.md": 40, "NOTES.txt": 10, "LICENSE": 5}),
                 mr({"src/login_handler.py": 80, "db/schema.sql": 25,
                     ".env.example": 6})]
        for _ in range(20):
            n = rng.randint(1, 30)
            # random pool: CODE/TEST ONLY — affinity preload (unbounded by
            # design, covered by test_affinity_overload_still_exit0) and
            # docs-pass saturation (enumerated docs-only case below) are
            # excluded so the asserted bound is the PROVABLE remainder-pass
            # bound. (Note "src/auth%02d.py" was never affinity-classified —
            # segment splits to auth05 — so keeping it would only imply
            # false coverage; it is removed.)
            pool = ["src/mod%02d.py" % rng.randint(0, 40),
                    "tests/test_mod%02d.py" % rng.randint(0, 40),
                    "src/service%02d.py" % rng.randint(0, 40)]
            files = {}
            for _ in range(n):
                files[rng.choice(pool)] = rng.choice(
                    [0, 1, 3, 10, 40, 120, 300])
            cases.append(mr(files))
        for i, data in enumerate(cases):
            with self.subTest(case=i):
                first = mod.partition(data)
                self.assertEqual(json.dumps(first),
                                 json.dumps(mod.partition(data)))
                owned = [p for a in AGENTS
                         for p in first["agents"][a]["files"]]
                self.assertEqual(sorted(owned),
                                 sorted(data["files_changed"]))
                total = sum(first["agents"][a]["weight_total"]
                            for a in AGENTS)
                if not total:
                    continue
                wmax = max(mod.weight(p, mod.added_lines(data, p))
                           for p in data["files_changed"])
                for a in AGENTS:
                    # remainder-pass bound: provable on this pool (no
                    # affinity preload, no docs saturation); the enumerated
                    # docs-only and all-security cases are hand-verified
                    self.assertLessEqual(
                        12 * first["agents"][a]["weight_total"],
                        5 * total + 12 * wmax, a)


if __name__ == "__main__":
    unittest.main()
