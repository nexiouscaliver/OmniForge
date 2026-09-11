"""WP-P3 delta-review tests — threshold config, anchoring-safe overlay,
dispatch caps.

Three P3 surfaces, all pure/offline:

1. **Configurable residual threshold** (`omni_sweep.residual_threshold`
   gains a per-project config; rev-3 defaults unchanged when config is
   None). The E3 seam already emits `residual_decision.threshold_met` with
   the defaults; P3 makes the knobs overridable per project
   (`--threshold-config` / `OMNIFORGE_DELTA_THRESHOLD`, consumed by the
   engine dispatcher when it invokes the sweep).
2. **Anchoring-safe delta scoping** (`omni_delta.apply_delta_overlay`):
   the full-MR partition stays the anchor truth — the overlay is a FILE-SET
   restriction at the prepare/partition layer; agents deep-dive only
   delta_files ∩ full-MR_files; the cross-cutting sweep stays ALL changed
   files. NEVER a --since-sha gather (delta-relative line numbers would
   mis-anchor threads).
3. **Dispatch caps** (`omni_delta.delta_review_decision`): one delta
   review per push batch, a daily cap, never on non-ancestor deltas
   (ancestry is the dispatcher's job — the plugin consumes its result),
   threshold consumed from the sweep result, never recomputed.
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW_SCRIPTS = os.path.abspath(os.path.join(
    HERE, "..", "skills", "omnireview-gitlab", "scripts"))
CHECK_SCRIPTS = os.path.abspath(os.path.join(
    HERE, "..", "skills", "omnicheck-gitlab", "scripts"))
FIXTURES = os.path.join(HERE, "fixtures")


def _load(name, scripts_dir, mod_name):
    spec = importlib.util.spec_from_file_location(
        mod_name, os.path.join(scripts_dir, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


omni_sweep = _load("omni_sweep", CHECK_SCRIPTS, "p3_omni_sweep")
omni_partition = _load("omni_partition", REVIEW_SCRIPTS, "p3_omni_partition")
try:
    omni_delta = _load("omni_delta", REVIEW_SCRIPTS, "p3_omni_delta")
except FileNotFoundError:
    omni_delta = None  # RED state: omni_delta.py does not exist yet


def _require_delta():
    if omni_delta is None:
        raise AssertionError(
            "omni_delta.py does not exist yet (WP-P3 GREEN deliverable)")


def p3_residual(files=None, additions=0, shapes=None):
    return {"files": files or [], "additions": additions, "deletions": 0,
            "shapes": shapes or []}


def p3_finding(fid, body, file_path, line):
    return {"id": fid, "disposition": "not_fixed", "severity": "important",
            "kind": "code", "file_path": file_path, "line_number": line,
            "body": body, "reason": ""}


# ── 1. Configurable residual threshold ────────────────────────────────────

class ThresholdConfigTests(unittest.TestCase):
    """rev-3 defaults are pinned; every knob is overridable; bad config is
    loud. Default-behavior equivalence with the E3 default flag is pinned
    by the matrix below (config=None must equal the shipped defaults)."""

    def test_p3_default_files_rule_trips_above_3(self):
        met, reason = omni_sweep.residual_threshold(
            p3_residual(files=["a.py", "b.py", "c.py", "d.py"],
                        shapes=["tests-only"]))
        self.assertTrue(met)
        self.assertIn("4 residual files", reason)

    def test_p3_default_files_rule_exactly_3_does_not_trip(self):
        # 3 test-only files, small, no code/manifest shapes -> quiet
        met, _ = omni_sweep.residual_threshold(
            p3_residual(files=["tests/a.py", "tests/b.py", "tests/c.py"],
                        additions=10, shapes=["tests-only"]))
        self.assertFalse(met)

    def test_p3_default_additions_rule_trips_above_50(self):
        met, reason = omni_sweep.residual_threshold(
            p3_residual(files=["tests/a.py"], additions=51,
                        shapes=["tests-only"]))
        self.assertTrue(met)
        self.assertIn("additions", reason)

    def test_p3_default_non_test_content_trips(self):
        met, reason = omni_sweep.residual_threshold(
            p3_residual(files=["src/one.py"], additions=2, shapes=["code"]))
        self.assertTrue(met)
        met, reason = omni_sweep.residual_threshold(
            p3_residual(files=["db/x.py"], additions=2, shapes=["migration"]))
        self.assertTrue(met)

    def test_p3_default_manifest_ci_shapes_trip(self):
        for shape in ("manifest", "ci"):
            met, reason = omni_sweep.residual_threshold(
                p3_residual(files=["pyproject.toml"], shapes=[shape]))
            self.assertTrue(met, shape)

    def test_p3_config_files_min_override(self):
        four_tests = p3_residual(files=["t1.py", "t2.py", "t3.py", "t4.py"],
                                 additions=2, shapes=["tests-only"])
        # default: 4 files > 3 trips
        self.assertTrue(omni_sweep.residual_threshold(four_tests)[0])
        # override: files_min 5 -> 4 files do not trip
        met, _ = omni_sweep.residual_threshold(
            four_tests, config={"files_min": 5})
        self.assertFalse(met)

    def test_p3_config_additions_min_override(self):
        sixty = p3_residual(files=["tests/a.py"], additions=60,
                            shapes=["tests-only"])
        self.assertTrue(omni_sweep.residual_threshold(sixty)[0])
        met, _ = omni_sweep.residual_threshold(
            sixty, config={"additions_min": 100})
        self.assertFalse(met)

    def test_p3_config_non_test_content_disabled(self):
        one_code = p3_residual(files=["src/one.py"], additions=2,
                               shapes=["code", "new file"])
        self.assertTrue(omni_sweep.residual_threshold(one_code)[0])
        met, _ = omni_sweep.residual_threshold(
            one_code, config={"non_test_content": False})
        self.assertFalse(met)

    def test_p3_config_manifest_ci_disabled(self):
        manifest = p3_residual(files=["pyproject.toml"], shapes=["manifest"])
        self.assertTrue(omni_sweep.residual_threshold(manifest)[0])
        met, _ = omni_sweep.residual_threshold(
            manifest, config={"manifest_ci": False})
        self.assertFalse(met)

    def test_p3_validate_threshold_config_accepts_partial(self):
        cfg = omni_sweep.validate_threshold_config({"files_min": 10})
        self.assertEqual(cfg, {"files_min": 10})

    def test_p3_validate_threshold_config_rejects_shapes(self):
        for bad in ("nope", 7, ["x"], None):
            with self.assertRaises(ValueError):
                omni_sweep.validate_threshold_config(bad)

    def test_p3_validate_threshold_config_rejects_unknown_key(self):
        with self.assertRaises(ValueError) as cm:
            omni_sweep.validate_threshold_config({"files_min": 1,
                                                  "mystery": 2})
        self.assertIn("mystery", str(cm.exception))

    def test_p3_validate_threshold_config_rejects_bad_types(self):
        for bad in ({"files_min": "3"}, {"files_min": -1},
                    {"additions_min": True}, {"non_test_content": "yes"},
                    {"manifest_ci": 0}):
            with self.assertRaises(ValueError):
                omni_sweep.validate_threshold_config(bad)

    def test_p3_threshold_config_from_spec_json_and_atfile(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "cfg.json")
            with open(path, "w") as fh:
                fh.write('{"files_min": 8}')
            self.assertEqual(
                omni_sweep.threshold_config_from_spec('{"files_min": 7}'),
                {"files_min": 7})
            self.assertEqual(
                omni_sweep.threshold_config_from_spec("@" + path),
                {"files_min": 8})

    def test_p3_threshold_config_from_spec_bad_is_loud(self):
        for bad in ("{not json", '{"unknown_k": 1}', "@/nonexistent-p3"):
            with self.assertRaises(ValueError):
                omni_sweep.threshold_config_from_spec(bad)


class SweepMainThresholdTests(unittest.TestCase):
    """--threshold-config (and the env fallback) flow into the result's
    residual_decision — the seam surface the engine dispatcher reads."""

    def _p3_repo(self, td):
        path = os.path.join(td, "repo")
        os.makedirs(path)

        def git(*args):
            return subprocess.run(["git", "-C", path, *args], check=True,
                                  capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        return path, git

    def _p3_run_main(self, argv, env=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with mock.patch.dict(os.environ, env or {}, clear=False):
                rc = omni_sweep.main(argv)
        return rc, json.loads(
            [l for l in out.getvalue().splitlines() if l.strip()][-1])

    def test_p3_main_threshold_config_flag_overrides_default(self):
        with tempfile.TemporaryDirectory() as td:
            path, git = self._p3_repo(td)
            with open(os.path.join(path, "base.txt"), "w") as fh:
                fh.write("base\n")
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            reviewed = subprocess.run(
                ["git", "-C", path, "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip()
            # one new code file: default threshold trips on the code shape
            os.makedirs(os.path.join(path, "src"))
            with open(os.path.join(path, "src", "thing.py"), "w") as fh:
                fh.write("x = 1\n")
            git("add", "-A")
            git("commit", "-q", "-m", "new code")
            head = subprocess.run(
                ["git", "-C", path, "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip()
            findings = os.path.join(td, "findings.json")
            with open(findings, "w") as fh:
                json.dump([], fh)
            base_argv = ["--repo-root", path, "--reviewed-head", reviewed,
                         "--head", head, "--findings", findings,
                         "--model", "none"]

            rc, result = self._p3_run_main(base_argv)
            self.assertEqual(rc, 0)
            self.assertTrue(result["residual_decision"]["threshold_met"])
            self.assertIn("code", result["residual"]["shapes"])

            rc, result = self._p3_run_main(
                base_argv + ["--threshold-config",
                             '{"non_test_content": false}'])
            self.assertEqual(rc, 0)
            self.assertFalse(result["residual_decision"]["threshold_met"])
            self.assertEqual(result["residual_decision"]["config"],
                             {"non_test_content": False})

    def test_p3_main_env_fallback_and_cli_precedence(self):
        with tempfile.TemporaryDirectory() as td:
            path, git = self._p3_repo(td)
            with open(os.path.join(path, "base.txt"), "w") as fh:
                fh.write("base\n")
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            reviewed = subprocess.run(
                ["git", "-C", path, "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip()
            os.makedirs(os.path.join(path, "src"))
            with open(os.path.join(path, "src", "thing.py"), "w") as fh:
                fh.write("x = 1\n")
            git("add", "-A")
            git("commit", "-q", "-m", "new code")
            head = subprocess.run(
                ["git", "-C", path, "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip()
            findings = os.path.join(td, "findings.json")
            with open(findings, "w") as fh:
                json.dump([], fh)
            base_argv = ["--repo-root", path, "--reviewed-head", reviewed,
                         "--head", head, "--findings", findings,
                         "--model", "none"]

            # env alone disables the code-shape trip
            rc, result = self._p3_run_main(
                base_argv, env={"OMNIFORGE_DELTA_THRESHOLD":
                                '{"non_test_content": false}'})
            self.assertEqual(rc, 0)
            self.assertFalse(result["residual_decision"]["threshold_met"])

            # CLI REPLACES env (never merged): CLI '{}' = all defaults ->
            # trips, proving the env value did not leak in
            rc, result = self._p3_run_main(
                base_argv + ["--threshold-config", "{}"],
                env={"OMNIFORGE_DELTA_THRESHOLD":
                     '{"non_test_content": false}'})
            self.assertEqual(rc, 0)
            self.assertTrue(result["residual_decision"]["threshold_met"])

            # a bad config is fatal before any sweep work
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                with mock.patch.dict(os.environ, {}, clear=False):
                    rc = omni_sweep.main(
                        base_argv + ["--threshold-config", "{bad"])
            self.assertEqual(rc, 2)


# ── 2. Anchoring-safe file-set overlay ────────────────────────────────────

def golden_partition():
    with open(os.path.join(FIXTURES, "prepare", "gather_input.json"),
              encoding="utf-8") as fh:
        gather = json.load(fh)
    return omni_partition.partition(gather["data"])


class LoadDeltaSpecTests(unittest.TestCase):

    def setUp(self):
        _require_delta()

    """The dispatcher hands prepare a delta spec file; accepted shapes are
    a bare array, {"files": [...]}, or the sweep-result envelope {"residual":
    {"files": [...]}} (whatever the seam has on disk)."""

    def test_p3_load_array_shape(self):
        files, reason = omni_delta.load_delta_spec(os.path.join(
            FIXTURES, "p3_delta_files.json"))
        self.assertEqual(reason, "ok")
        self.assertEqual(files, ["src/app.py", "src/auth_check.py",
                                 "src/gone.py"])

    def test_p3_load_sweep_result_shape(self):
        files, reason = omni_delta.load_delta_spec(os.path.join(
            FIXTURES, "p3_delta_spec_object.json"))
        self.assertEqual(reason, "ok")
        self.assertEqual(files, ["src/app.py", "src/auth_check.py"])

    def test_p3_load_errors(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", d]))
        bad_json = os.path.join(d, "bad.json")
        with open(bad_json, "w") as fh:
            fh.write("{not json")
        for path, want in (
                (os.path.join(d, "missing.json"), "file not found"),
                (bad_json, "invalid JSON"),
                (os.path.join(FIXTURES, "p3_delta_spec_bad.json"),
                 "unrecognized shape"),
        ):
            files, reason = omni_delta.load_delta_spec(path)
            self.assertIsNone(files, path)
            self.assertEqual(reason, want)

    def test_p3_load_rejects_non_string_entries(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", d]))
        p = os.path.join(d, "entries.json")
        with open(p, "w") as fh:
            json.dump(["a.py", 7], fh)
        files, reason = omni_delta.load_delta_spec(p)
        self.assertIsNone(files)
        self.assertEqual(reason, "unrecognized shape")


class OverlayTests(unittest.TestCase):

    def setUp(self):
        _require_delta()

    """The overlay restricts deep-dive ownership to delta ∩ full-MR files.
    The full-MR partition (files list + cross-cutting set) is the anchor
    truth and must survive untouched — anchors for threads come from the
    full-MR diff_line_map."""

    DELTA = ["src/app.py", "src/auth_check.py", "src/gone.py"]

    def test_p3_overlay_scopes_ownership_to_intersection(self):
        partition = golden_partition()
        overlaid = omni_delta.apply_delta_overlay(partition, self.DELTA)
        self.assertEqual(overlaid["agents"]["analyst"]["files"], [])
        self.assertEqual(overlaid["agents"]["codebase"]["files"],
                         ["src/app.py"])
        self.assertEqual(overlaid["agents"]["security"]["files"],
                         ["src/auth_check.py"])
        # added_lines_total recomputed over the surviving owned files
        self.assertEqual(overlaid["agents"]["analyst"]
                         ["added_lines_total"], 0)
        self.assertEqual(overlaid["agents"]["codebase"]
                         ["added_lines_total"], 3)
        self.assertEqual(overlaid["agents"]["security"]
                         ["added_lines_total"], 2)

    def test_p3_overlay_preserves_full_mr_truth(self):
        partition = golden_partition()
        overlaid = omni_delta.apply_delta_overlay(partition, self.DELTA)
        # the files list and cross-cutting sweep stay the FULL MR set
        self.assertEqual([f["path"] for f in overlaid["files"]],
                         [f["path"] for f in partition["files"]])
        for agent in ("analyst", "codebase", "security"):
            self.assertEqual(overlaid["agents"][agent]["cross_cutting_files"],
                             partition["agents"][agent]["cross_cutting_files"])

    def test_p3_overlay_does_not_mutate_input(self):
        partition = golden_partition()
        before = json.dumps(partition, sort_keys=True)
        omni_delta.apply_delta_overlay(partition, self.DELTA)
        self.assertEqual(json.dumps(partition, sort_keys=True), before)

    def test_p3_overlay_empty_intersection_owns_nothing(self):
        partition = golden_partition()
        overlaid = omni_delta.apply_delta_overlay(
            partition, ["some/other_file.py"])
        for agent in ("analyst", "codebase", "security"):
            self.assertEqual(overlaid["agents"][agent]["files"], [])
        self.assertEqual(len(overlaid["files"]), len(partition["files"]))

    def test_p3_overlay_scoped_file_list(self):
        overlaid = omni_delta.apply_delta_overlay(
            golden_partition(), self.DELTA)
        self.assertEqual(sorted(omni_delta.scoped_files(overlaid)),
                         ["src/app.py", "src/auth_check.py"])


# ── 3. Dispatch caps ──────────────────────────────────────────────────────

class DecisionTests(unittest.TestCase):

    def setUp(self):
        _require_delta()

    """Guard order: skip → non-ancestor → batch-done → daily cap →
    threshold. The plugin never computes ancestry or counters — the
    dispatcher passes them in."""

    SWEEP_OK = {"skip": None,
                "residual_decision": {"threshold_met": True,
                                      "reason": "5 residual files > 3"}}

    def test_p3_dispatches_when_threshold_met(self):
        d = omni_delta.delta_review_decision(self.SWEEP_OK)
        self.assertTrue(d["dispatch"])
        self.assertIn("5 residual files", d["reason"])

    def test_p3_skipped_sweep_never_dispatches(self):
        d = omni_delta.delta_review_decision(
            {"skip": "SKIP_OPT_OUT",
             "residual_decision": {"threshold_met": True, "reason": ""}})
        self.assertFalse(d["dispatch"])
        self.assertIn("SKIP_OPT_OUT", d["reason"])

    def test_p3_non_ancestor_never_dispatches(self):
        d = omni_delta.delta_review_decision(self.SWEEP_OK,
                                             is_ancestor=False)
        self.assertFalse(d["dispatch"])
        self.assertIn("non-ancestor", d["reason"])

    def test_p3_one_review_per_batch(self):
        d = omni_delta.delta_review_decision(self.SWEEP_OK,
                                             batch_delta_review_done=True)
        self.assertFalse(d["dispatch"])
        self.assertIn("batch", d["reason"])

    def test_p3_daily_cap(self):
        d = omni_delta.delta_review_decision(self.SWEEP_OK,
                                             delta_reviews_today=1,
                                             daily_cap=1)
        self.assertFalse(d["dispatch"])
        self.assertIn("daily cap", d["reason"])
        # under the cap: fine
        d = omni_delta.delta_review_decision(self.SWEEP_OK,
                                             delta_reviews_today=1,
                                             daily_cap=2)
        self.assertTrue(d["dispatch"])

    def test_p3_threshold_not_met(self):
        d = omni_delta.delta_review_decision(
            {"skip": None,
             "residual_decision": {"threshold_met": False, "reason": ""}})
        self.assertFalse(d["dispatch"])
        self.assertIn("threshold", d["reason"].lower())

    def test_p3_missing_residual_decision_is_not_met(self):
        d = omni_delta.delta_review_decision({"skip": None})
        self.assertFalse(d["dispatch"])

    def test_p3_default_daily_cap_is_one(self):
        d = omni_delta.delta_review_decision(self.SWEEP_OK,
                                             delta_reviews_today=1)
        self.assertFalse(d["dispatch"])


class DecisionCLITests(unittest.TestCase):

    def setUp(self):
        _require_delta()

    """The dispatcher consumes this CLI later; the output is ONE JSON
    line. dispatch:false is a DECISION (exit 0), not an error."""

    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = omni_delta.main(argv)
        return rc, json.loads(
            [l for l in out.getvalue().splitlines() if l.strip()][-1])

    def test_p3_cli_dispatch_true(self):
        rc, d = self._run([
            "--sweep-result", os.path.join(FIXTURES, "p3_sweep_result.json")])
        self.assertEqual(rc, 0)
        self.assertTrue(d["dispatch"])
        self.assertIn("5 residual files", d["reason"])

    def test_p3_cli_no_ancestor(self):
        rc, d = self._run([
            "--sweep-result", os.path.join(FIXTURES, "p3_sweep_result.json"),
            "--no-ancestor"])
        self.assertEqual(rc, 0)
        self.assertFalse(d["dispatch"])
        self.assertIn("non-ancestor", d["reason"])

    def test_p3_cli_batch_done_and_caps(self):
        base = ["--sweep-result",
                os.path.join(FIXTURES, "p3_sweep_result.json")]
        rc, d = self._run(base + ["--batch-done"])
        self.assertFalse(d["dispatch"])
        rc, d = self._run(base + ["--reviews-today", "1"])
        self.assertFalse(d["dispatch"])
        rc, d = self._run(base + ["--reviews-today", "1",
                                  "--daily-cap", "3"])
        self.assertTrue(d["dispatch"])

    def test_p3_cli_missing_sweep_result_is_usage_error(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = omni_delta.main(["--sweep-result",
                                  "/nonexistent-p3-sweep.json"])
        self.assertEqual(rc, 2)
        self.assertIn("omni_delta", err.getvalue())


if __name__ == "__main__":
    unittest.main()
