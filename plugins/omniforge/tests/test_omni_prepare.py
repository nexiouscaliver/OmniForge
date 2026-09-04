"""Contract tests for the deterministic Phase-1 pre-dispatch (omni_prepare.py).

omni_prepare.py collapses the improvised Phase 1 (fetch -> partition -> briefs)
into ONE script: it runs omni_fetch_mr.py as a subprocess (the single HTTP
pass), omni_partition.py as a subprocess, renders the three reviewer briefs,
and writes prepare.json + phases.jsonl into the run dir.

Pinned contract (round2-pre-dispatch plan section 2 / spec sections 2-6):
- CLI: --project --iid --review-id --run-dir required; optional
  --prior-report, --verify-head, --dry-run. --iid maps to the fetcher's --mr.
- --dry-run: one plan JSON line, no network/mkdir/writes, no token needed;
  a bad --prior-report is REPORTED (exists/valid_json), never fatal.
- exit codes: 0 ok; 1 gather/partition/internal failure; 2 usage or IO;
  3 auth (missing token, or fetch child exit 1 whose error carries integer
  HTTP 401/403 — extracted via regex, never substring); 4 head moved.
- run-dir outputs: gather.json, partition.json, prepare.json (15 keys),
  briefs/agent-1..3.md, phases.jsonl append (7 keys); stdout = ONE receipt
  line (11 keys). Subprocess stdout never leaks.
- determinism: briefs are byte-identical given identical gather.json (the
  only timestamp in a brief is gather.fetched_at, verbatim).

Harness conventions (spec section 9): stdlib unittest, offline, importlib
script loading; the network is faked with a stdlib ThreadingHTTPServer on
127.0.0.1 whose routes table is ordered most-specific-first (the bare MR
path LAST — it is a substring of every sub-endpoint) and whose handler
records (method, path) into a shared log. GITLAB_HOST=http://127.0.0.1:<port>
reaches BOTH subprocess layers hermetically. run_prepare scrubs
GITLAB_TOKEN/OMNIFORGE_GITLAB_TOKEN/GITLAB_HOST/CI_API_V4_URL the way
test_omni_fetch_mr.run_fetch does.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
    "skills", "omnireview-gitlab", "scripts"))
PREPARE = os.path.join(SCRIPTS, "omni_prepare.py")

sys.path.insert(0, SCRIPTS)
import omni_glab_api  # noqa: E402  (same module object the script imports)

PROJECT = "73279395"
MR = "21"

_PREPARE = None


def load_prepare():
    """Load omni_prepare.py under a stable unique name (cached per process
    so in-process monkeypatching of one test reaches main())."""
    global _PREPARE
    if _PREPARE is None:
        spec = importlib.util.spec_from_file_location(
            "omni_prepare_under_test", PREPARE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PREPARE = mod
    return _PREPARE


def tmp_dir(testcase):
    d = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


def run_prepare(argv, token="test-token-1", host=None):
    """Run main() in-process with the token/host env scrubbed exactly like
    test_omni_fetch_mr.run_fetch. token=None strips both token env vars.
    argparse's natural SystemExit(2) surfaces as rc=2."""
    mod = load_prepare()
    env = {}
    if token is not None:
        env["GITLAB_TOKEN"] = token
    if host is not None:
        env["GITLAB_HOST"] = host
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env, clear=False):
        for key in ("GITLAB_TOKEN", "OMNIFORGE_GITLAB_TOKEN",
                    "GITLAB_HOST", "CI_API_V4_URL"):
            if key not in env:
                os.environ.pop(key, None)
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                rc = mod.main([str(x) for x in argv])
            except SystemExit as e:
                rc = e.code
    return rc, out.getvalue(), err.getvalue()


def stdout_json(text):
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) != 1:
        raise ValueError("expected exactly one stdout JSON line, got %d: %r" % (
            len(lines), text[:400]))
    return json.loads(lines[0])


class PrepareDryRunTests(unittest.TestCase):
    def test_prepare_dry_run_plan_line_shape(self):
        d = tmp_dir(self)
        run_dir = os.path.join(d, "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "rev-1",
             "--run-dir", run_dir, "--dry-run", "--verify-head", "head1"])
        self.assertEqual(rc, 0, se)
        plan = stdout_json(so)
        self.assertEqual(set(plan),
                         {"ok", "dry_run", "run_dir", "project", "mr_iid",
                          "review_id", "outputs", "prior_report",
                          "token_present", "verify_head"})
        self.assertIs(plan["ok"], True)
        self.assertIs(plan["dry_run"], True)
        self.assertEqual(plan["run_dir"], os.path.abspath(run_dir))
        self.assertEqual(plan["project"], PROJECT)
        self.assertEqual(plan["mr_iid"], MR)
        self.assertEqual(plan["review_id"], "rev-1")
        self.assertEqual(plan["outputs"], {
            "prepare_json": os.path.join(run_dir, "prepare.json"),
            "gather_json": os.path.join(run_dir, "gather.json"),
            "partition_json": os.path.join(run_dir, "partition.json"),
            "briefs": [os.path.join(run_dir, "briefs", "agent-1.md"),
                       os.path.join(run_dir, "briefs", "agent-2.md"),
                       os.path.join(run_dir, "briefs", "agent-3.md")],
            "phases_jsonl": os.path.join(run_dir, "phases.jsonl")})
        self.assertIsNone(plan["prior_report"])
        self.assertIs(plan["token_present"], True)
        self.assertEqual(plan["verify_head"], "head1")
        # no --verify-head -> null
        rc2, so2, se2 = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "rev-1",
             "--run-dir", run_dir, "--dry-run"])
        self.assertEqual(rc2, 0, se2)
        self.assertIsNone(stdout_json(so2)["verify_head"])

    def test_prepare_dry_run_no_writes_no_mkdir(self):
        d = tmp_dir(self)
        run_dir = os.path.join(d, "never-created")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "rev-1",
             "--run-dir", run_dir, "--dry-run"], token=None)
        self.assertEqual(rc, 0, se)          # no token needed for dry-run
        self.assertFalse(os.path.exists(run_dir))
        self.assertEqual(os.listdir(d), [])  # nothing written anywhere

    def test_prepare_dry_run_bad_prior_report_reported_not_fatal(self):
        d = tmp_dir(self)
        missing = os.path.join(d, "absent.json")
        invalid = os.path.join(d, "invalid.json")
        with open(invalid, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        valid = os.path.join(d, "valid.json")
        with open(valid, "w", encoding="utf-8") as fh:
            json.dump([{"a": 1}, {"b": 2}], fh)
        for path, exists, valid_json, count in (
                (missing, False, False, None),
                (invalid, True, False, None),
                (valid, True, True, 2)):
            with self.subTest(path=path):
                rc, so, se = run_prepare(
                    ["--project", PROJECT, "--iid", MR, "--review-id", "r",
                     "--run-dir", os.path.join(d, "run"), "--dry-run",
                     "--prior-report", path])
                self.assertEqual(rc, 0, se)  # reported, never fatal
                stat = stdout_json(so)["prior_report"]
                self.assertEqual(stat, {"path": path, "exists": exists,
                                        "valid_json": valid_json,
                                        "findings_count": count})

    def test_prepare_dry_run_token_present_flag_true_and_false(self):
        d = tmp_dir(self)
        argv = ["--project", PROJECT, "--iid", MR, "--review-id", "r",
                "--run-dir", os.path.join(d, "run"), "--dry-run"]
        rc, so, se = run_prepare(argv, token="tok")
        self.assertIs(stdout_json(so)["token_present"], True)
        rc2, so2, se2 = run_prepare(argv, token=None)
        self.assertIs(stdout_json(so2)["token_present"], False)

    def test_prepare_usage_error_exit_2(self):
        # missing required flags -> argparse's natural exit 2, no stdout JSON
        rc, so, se = run_prepare(["--project", PROJECT, "--iid", MR])
        self.assertEqual(rc, 2)
        self.assertIn("usage", se)
        self.assertEqual(so.strip(), "")


if __name__ == "__main__":
    unittest.main()
