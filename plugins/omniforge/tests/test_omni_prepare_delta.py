"""WP-P3 prepare-integration tests — the delta file-set overlay rides the
REAL omni_prepare.py flow over the http-server harness.

Anchoring contract under test (rev-2 tier 1, load-bearing): the full-MR
gather (6 GETs) and the on-disk partition.json stay EXACTLY as today —
they are the anchor truth; the delta arrives as --delta-files +
--delta-base and scopes only the deep-dive layer (briefs owned lists,
prepare.json partitions). NEVER a --since-sha gather: anchors for threads
come from the full-MR diff_line_map.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(
    HERE, "..", "skills", "omnireview-gitlab", "scripts"))
FIXTURES = os.path.join(HERE, "fixtures")
PREPARE_FIXTURES = os.path.join(FIXTURES, "prepare")

sys.path.insert(0, SCRIPTS)
import omni_glab_api  # noqa: E402

PROJECT = "73279395"
MR = "21"

_PREPARE = None


def load_prepare():
    global _PREPARE
    if _PREPARE is None:
        spec = importlib.util.spec_from_file_location(
            "omni_prepare_p3_under_test", os.path.join(SCRIPTS,
                                                       "omni_prepare.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PREPARE = mod
    return _PREPARE


def tmp_dir(testcase):
    d = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


def run_prepare(argv, token="test-token-1", host=None):
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
        raise ValueError("expected exactly one stdout JSON line, got %d: %r"
                         % (len(lines), text[:400]))
    return json.loads(lines[0])


def make_http_server(testcase, routes):
    requests_log = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests_log.append(("GET", self.path))
            for sub, status, body in routes:
                if sub in self.path:
                    self._respond(status, body)
                    return
            self._respond(404, {"error": "no route for %s" % self.path})

        def _respond(self, status, body):
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    def _teardown():
        server.shutdown()
        server.server_close()

    testcase.addCleanup(_teardown)
    return "http://127.0.0.1:%d" % server.server_address[1], requests_log


META = {
    "iid": 21,
    "title": "Add widget API",
    "description": "Adds the widget endpoint.",
    "author": {"username": "dev"},
    "source_branch": "feat/widget",
    "target_branch": "main",
    "head_pipeline": {"status": "success"},
    "diff_refs": {"base_sha": "b000", "head_sha": "head1", "start_sha": "s000"},
    "labels": ["backend"],
    "assignees": [{"username": "a1"}],
    "reviewers": [{"username": "r1"}],
}

APP_HUNK = """@@ -40,6 +40,9 @@ def handler(cfg):
     context line
+    if cfg is None:
+        return None
+    return cfg.get("x")
     more context
"""

NET_HUNK = """@@ -10,4 +10,4 @@ def fetch(url):
     ctx
-    old_endpoint = "http://hardcoded"
+    new_endpoint = url
     tail
"""

README_HUNK = """@@ -1,2 +1,5 @@
 # Project
+
+## Overview
+
"""

AUTH_HUNK = """@@ -20,4 +20,6 @@ def check(user):
     ctx
+    if not user.is_active:
+        raise PermissionError("inactive")
     return True
"""

DIFF_PAGE = [
    {"old_path": "src/app.py", "new_path": "src/app.py", "diff": APP_HUNK},
    {"old_path": "src/net.py", "new_path": "src/net.py", "diff": NET_HUNK},
    {"old_path": "README.md", "new_path": "README.md", "diff": README_HUNK},
    {"old_path": "src/auth_check.py", "new_path": "src/auth_check.py",
     "diff": AUTH_HUNK},
]

RAW_DISCUSSIONS = [
    {"id": "d1", "resolvable": True, "resolved": False,
     "notes": [
         {"system": False, "type": "DiffNote", "body": "please guard cfg",
          "author": {"username": "rev1"},
          "created_at": "2026-09-01T10:00:00Z",
          "position": {"new_path": "src/app.py", "new_line": 41,
                       "position_type": "text"}},
     ]},
]

RAW_COMMITS = [
    {"id": "c111", "title": "Add widget"},
    {"id": "c222", "title": "Fix lint"},
]

VERSIONS = [{"id": 1, "head_commit_sha": "head1",
             "created_at": "2026-09-01T09:00:00Z"}]

RAW_NOTES = [
    {"system": False, "body": "top-level note",
     "author": {"username": "rev3"},
     "created_at": "2026-09-01T12:00:00Z"},
]


def default_routes():
    return [
        ("/diffs", 200, DIFF_PAGE),
        ("/discussions", 200, RAW_DISCUSSIONS),
        ("/commits", 200, RAW_COMMITS),
        ("/versions", 200, VERSIONS),
        ("/notes", 200, RAW_NOTES),
        ("/merge_requests/" + MR, 200, META),
    ]


DELTA_FILES = os.path.join(FIXTURES, "p3_delta_files.json")
DELTA_SPEC_OBJECT = os.path.join(FIXTURES, "p3_delta_spec_object.json")
DELTA_SPEC_BAD = os.path.join(FIXTURES, "p3_delta_spec_bad.json")
DELTA_BASE = "reviewed0head0000000000000000000000000000cafe"


class PrepareDeltaRunTests(unittest.TestCase):
    """The full delta-review Phase 1: gather unchanged (one HTTP pass),
    partition.json on disk = full-MR truth, briefs + prepare.json scoped."""

    def _run_delta(self, delta_path=DELTA_FILES, base=DELTA_BASE,
                   review_id="rev-p3"):
        base_url, log = make_http_server(self, default_routes())
        run_dir = os.path.join(tmp_dir(self), "run")
        argv = ["--project", PROJECT, "--iid", MR,
                "--review-id", review_id, "--run-dir", run_dir,
                "--delta-files", delta_path, "--delta-base", base]
        rc, so, se = run_prepare(argv, host=base_url)
        return rc, so, se, run_dir, log

    def test_p3_delta_run_ok_receipt_unchanged_11_keys(self):
        rc, so, se, run_dir, _ = self._run_delta()
        self.assertEqual(rc, 0, se)
        receipt = stdout_json(so)
        self.assertEqual(set(receipt), {
            "ok", "run_dir", "project", "mr_iid", "review_id", "head_sha",
            "files", "added_lines", "partitions", "prior_findings",
            "duration_s"})
        # full-MR totals: the gather scope never shrinks
        self.assertEqual(receipt["files"], 4)
        self.assertEqual(receipt["added_lines"], 9)

    def test_p3_partition_json_on_disk_stays_full_mr_truth(self):
        rc, so, se, run_dir, _ = self._run_delta()
        self.assertEqual(rc, 0, se)
        with open(os.path.join(run_dir, "partition.json"),
                  encoding="utf-8") as fh:
            partition = json.load(fh)
        self.assertEqual(len(partition["files"]), 4)
        # every full-MR file keeps an owner — the anchor truth is intact
        owners = {f["path"]: f["owner"] for f in partition["files"]}
        self.assertEqual(owners, {
            "src/app.py": "codebase", "src/net.py": "codebase",
            "README.md": "analyst", "src/auth_check.py": "security"})
        self.assertEqual(len(partition["agents"]["analyst"]["files"]), 1)
        self.assertEqual(len(partition["agents"]["codebase"]["files"]), 2)
        self.assertEqual(len(partition["agents"]["security"]["files"]), 1)

    def test_p3_prepare_json_records_delta_scope(self):
        rc, so, se, run_dir, _ = self._run_delta()
        self.assertEqual(rc, 0, se)
        with open(os.path.join(run_dir, "prepare.json"),
                  encoding="utf-8") as fh:
            doc = json.load(fh)
        # 15 base keys + exactly one additive delta key
        self.assertEqual(set(doc) - {"delta"}, {
            "schema", "created_at", "review_id", "project", "mr_iid",
            "head_sha", "run_dir", "gather_json", "partition_json",
            "briefs", "files", "added_lines", "partitions", "prior_report",
            "elapsed_ms"})
        self.assertEqual(doc["files"], 4)          # full-MR totals
        self.assertEqual(doc["added_lines"], 9)
        self.assertEqual(doc["delta"], {
            "base_sha": DELTA_BASE,
            "files": ["src/app.py", "src/auth_check.py"]})
        # partitions carry the SCOPED counts (what agents deep-dive)
        self.assertEqual(doc["partitions"], {
            "analyst": {"files": 0, "added_lines_total": 0},
            "codebase": {"files": 1, "added_lines_total": 3},
            "security": {"files": 1, "added_lines_total": 2}})

    def test_p3_briefs_scoped_with_delta_wording(self):
        rc, so, se, run_dir, _ = self._run_delta()
        self.assertEqual(rc, 0, se)
        with open(os.path.join(run_dir, "briefs", "agent-1.md"),
                  encoding="utf-8") as fh:
            analyst = fh.read()
        with open(os.path.join(run_dir, "briefs", "agent-2.md"),
                  encoding="utf-8") as fh:
            codebase = fh.read()
        with open(os.path.join(run_dir, "briefs", "agent-3.md"),
                  encoding="utf-8") as fh:
            security = fh.read()
        # the delta marker + base sha ride the Owned-files section (the
        # piece Phase 3 injects) — priors authoritative, never re-adjudiced
        for brief in (analyst, codebase, security):
            self.assertIn(DELTA_BASE, brief)
            self.assertIn("DELTA REVIEW", brief)
            self.assertIn("priors are authoritative", brief)
            self.assertIn("Delta scope: 2 of 4 MR files", brief)
        # analyst owns nothing post-overlay; codebase/security keep their
        # delta-intersecting files only
        self.assertIn("(none — no owned files)", analyst)
        self.assertNotIn("README.md", analyst.split("## Stats")[0])
        self.assertIn("- `src/app.py`", codebase)
        self.assertNotIn("`src/net.py`", codebase.split("## Stats")[0])
        self.assertIn("- `src/auth_check.py`", security)
        # cross-cutting sweep depth still names ALL changed files
        for brief in (analyst, codebase, security):
            self.assertIn("all 4 changed files", brief)

    def test_p3_delta_gather_is_the_same_one_http_pass(self):
        rc, so, se, run_dir, log = self._run_delta()
        self.assertEqual(rc, 0, se)
        # the full-MR gather ran exactly as a normal run (anchor truth):
        # the same GET set over the same 6 endpoints
        paths = [p for _, p in log]
        for needle in ("/diffs", "/discussions", "/commits", "/versions",
                       "/notes", "/merge_requests/" + MR):
            self.assertTrue(any(n in p for p in paths), needle)

    def test_p3_delta_spec_object_shape_accepted(self):
        rc, so, se, run_dir, _ = self._run_delta(
            delta_path=DELTA_SPEC_OBJECT)
        self.assertEqual(rc, 0, se)
        with open(os.path.join(run_dir, "prepare.json"),
                  encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["delta"]["files"],
                         ["src/app.py", "src/auth_check.py"])

    def test_p3_phases_line_keeps_seven_key_shape(self):
        rc, so, se, run_dir, _ = self._run_delta()
        self.assertEqual(rc, 0, se)
        with open(os.path.join(run_dir, "phases.jsonl"),
                  encoding="utf-8") as fh:
            line = json.loads(fh.read().splitlines()[-1])
        self.assertEqual(set(line), {
            "phase", "review_id", "duration_s", "files", "partitions",
            "head_sha", "ts"})
        self.assertEqual(line["files"], 4)


class PrepareDeltaUsageTests(unittest.TestCase):
    """--delta-files and --delta-base are a both-or-neither pair; a bad
    spec in a REAL run is fatal exit 2 (the --prior-report discipline)."""

    def test_p3_delta_files_without_base_exit_2(self):
        base_url, _ = make_http_server(self, default_routes())
        run_dir = os.path.join(tmp_dir(self), "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "r",
             "--run-dir", run_dir, "--delta-files", DELTA_FILES],
            host=base_url)
        self.assertEqual(rc, 2)
        self.assertIn("--delta-base", se)

    def test_p3_delta_base_without_files_exit_2(self):
        base_url, _ = make_http_server(self, default_routes())
        run_dir = os.path.join(tmp_dir(self), "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "r",
             "--run-dir", run_dir, "--delta-base", DELTA_BASE],
            host=base_url)
        self.assertEqual(rc, 2)
        self.assertIn("--delta-files", se)

    def test_p3_bad_delta_spec_real_run_fatal_exit_2(self):
        base_url, _ = make_http_server(self, default_routes())
        run_dir = os.path.join(tmp_dir(self), "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "r",
             "--run-dir", run_dir, "--delta-files", DELTA_SPEC_BAD,
             "--delta-base", DELTA_BASE],
            host=base_url)
        self.assertEqual(rc, 2)
        self.assertIn("omni_prepare: --delta-files", se)

    def test_p3_missing_delta_spec_real_run_fatal_exit_2(self):
        base_url, _ = make_http_server(self, default_routes())
        run_dir = os.path.join(tmp_dir(self), "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "r",
             "--run-dir", run_dir, "--delta-files",
             os.path.join(tmp_dir(self), "nope.json"),
             "--delta-base", DELTA_BASE],
            host=base_url)
        self.assertEqual(rc, 2)
        self.assertIn("file not found", se)


class PrepareDeltaDryRunTests(unittest.TestCase):
    """--dry-run validates the delta pair and reports the spec without
    judging the caller (the --prior-report convention)."""

    def test_p3_dry_run_delta_plan_carries_scope(self):
        run_dir = os.path.join(tmp_dir(self), "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "r",
             "--run-dir", run_dir, "--dry-run",
             "--delta-files", DELTA_FILES, "--delta-base", DELTA_BASE])
        self.assertEqual(rc, 0, se)
        plan = stdout_json(so)
        self.assertEqual(set(plan), {
            "ok", "dry_run", "run_dir", "project", "mr_iid", "review_id",
            "outputs", "prior_report", "token_present", "verify_head",
            "delta"})
        self.assertEqual(plan["delta"]["base_sha"], DELTA_BASE)
        self.assertTrue(plan["delta"]["spec"]["valid_json"])
        self.assertEqual(plan["delta"]["spec"]["files_count"], 3)

    def test_p3_dry_run_without_delta_flags_has_no_delta_key(self):
        run_dir = os.path.join(tmp_dir(self), "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "r",
             "--run-dir", run_dir, "--dry-run"])
        self.assertEqual(rc, 0, se)
        self.assertNotIn("delta", stdout_json(so))

    def test_p3_dry_run_half_pair_is_usage_error(self):
        run_dir = os.path.join(tmp_dir(self), "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "r",
             "--run-dir", run_dir, "--dry-run", "--delta-base", DELTA_BASE])
        self.assertEqual(rc, 2)

    def test_p3_dry_run_bad_delta_spec_reported_not_fatal(self):
        run_dir = os.path.join(tmp_dir(self), "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "r",
             "--run-dir", run_dir, "--dry-run",
             "--delta-files", DELTA_SPEC_BAD, "--delta-base", DELTA_BASE])
        self.assertEqual(rc, 0, se)
        plan = stdout_json(so)
        self.assertFalse(plan["delta"]["spec"]["valid_json"])
        self.assertIsNone(plan["delta"]["spec"]["files_count"])


if __name__ == "__main__":
    unittest.main()
