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
        # security owns ONLY its affinity set — never greedy overflow
        security_files = result["agents"]["security"]["files"]
        self.assertEqual(sorted(security_files),
                         sorted(["src/auth.py", "migrations/001_init.sql",
                                 ".gitlab-ci.yml"]))
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
        self.assertEqual(result["agents"]["security"]["files"], [])
        totals = {a: result["agents"][a]["added_lines_total"]
                  for a in ("analyst", "codebase")}
        largest = 400
        self.assertLessEqual(max(totals.values()) - min(totals.values()),
                             largest,
                         "greedy guarantee violated: %r" % totals)
        self.assertEqual(sum(totals.values()), 820)   # every line counted once
        for f in result["files"]:
            self.assertEqual(f["reason"], "greedy-balance", f)

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


if __name__ == "__main__":
    unittest.main()
