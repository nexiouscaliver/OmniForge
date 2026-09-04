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
import re
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


# ── http.server harness (the seam that reaches BOTH subprocess layers) ───
#
# ThreadingHTTPServer with daemon_threads=True (non-daemon per-request
# threads are a process-exit hang risk); teardown via addCleanup running
# server.shutdown() then server.server_close(); the handler serves a
# routes table [(path_substring, status, json_body)] ordered
# most-specific-first — the bare /merge_requests/{mr} path is a substring
# of every sub-endpoint and must sit LAST — and records (method, path)
# into a shared log; log_message is overridden to stay silent.

def make_http_server(testcase, routes):
    """Start a silent fake GitLab API on 127.0.0.1:<ephemeral>.

    Returns (base_url, requests_log). The log collects ("GET", path) for
    every request, including query strings, so one-pass GET sequences are
    assertable across the fetch grandchild subprocess.
    """
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

# The /diffs API returns per-file diffs starting directly at the @@ hunks
# (no diff --git/+++ headers); the fetcher synthesizes the headers. Four
# files so every agent owns something: src/app.py + src/net.py (generic ->
# greedy/codebase), README.md (docs -> analyst), src/auth_check.py
# (security-affinity -> security).
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
    # Order matters: substrings are matched most-specific first, the bare
    # MR path LAST (it is a substring of every sub-endpoint path).
    return [
        ("/diffs", 200, DIFF_PAGE),
        ("/discussions", 200, RAW_DISCUSSIONS),
        ("/commits", 200, RAW_COMMITS),
        ("/versions", 200, VERSIONS),
        ("/notes", 200, RAW_NOTES),
        ("/merge_requests/" + MR, 200, META),
    ]


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


class PrepareAuthTests(unittest.TestCase):
    def test_prepare_missing_token_exit_3_glab_stderr(self):
        d = tmp_dir(self)
        run_dir = os.path.join(d, "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "r",
             "--run-dir", run_dir], token=None)
        self.assertEqual(rc, 3)
        st = stdout_json(so)
        self.assertIs(st["ok"], False)
        self.assertEqual(st["error"], "auth")
        self.assertIn("detail", st)
        # stderr reuses the fetcher's glab extraction one-liner verbatim
        self.assertIn("glab auth status", se)
        self.assertIn("export GITLAB_TOKEN=$(glab auth status", se)

    def _auth_run(self, status):
        base, log = make_http_server(self, [
            ("/merge_requests/" + MR, status,
             {"message": "%d" % status}),
        ])
        d = tmp_dir(self)
        run_dir = os.path.join(d, "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "r",
             "--run-dir", run_dir], host=base)
        return rc, so, se, run_dir, log

    def test_prepare_http_401_during_gather_exit_3(self):
        rc, so, se, run_dir, log = self._auth_run(401)
        self.assertEqual(rc, 3)
        st = stdout_json(so)
        self.assertIs(st["ok"], False)
        self.assertEqual(st["error"], "auth")
        self.assertIn("HTTP 401", st["detail"])
        self.assertIn("glab auth status", se)

    def test_prepare_http_403_during_gather_exit_3(self):
        rc, so, se, run_dir, log = self._auth_run(403)
        self.assertEqual(rc, 3)
        st = stdout_json(so)
        self.assertIs(st["ok"], False)
        self.assertEqual(st["error"], "auth")
        self.assertIn("HTTP 403", st["detail"])
        self.assertIn("glab auth status", se)

    def test_prepare_glab_api_error_message_format_pinned(self):
        # R5: the status must be extractable as an INTEGER from the true
        # status position, so a future omni_glab_api refactor cannot
        # silently degrade exit 3 -> exit 1.
        for status in ("401", "403"):
            err = omni_glab_api.GlabApiError("GET", "/projects/p", int(status), "x")
            m = re.search(r"failed with HTTP (\d+)", str(err))
            self.assertIsNotNone(m, str(err))
            self.assertEqual(m.group(1), status)

    def test_prepare_classify_5xx_body_containing_literal_403_is_gather_failure(self):
        mod = load_prepare()
        # a 5xx whose redacted detail body contains the literal "HTTP 403"
        # extracts 503 -> gather failure, NOT auth (integer compare, never
        # substring)
        self.assertEqual(
            mod.classify_fetch(
                1, '{"ok":false,"error":"GET /p failed with HTTP 503: '
                   'upstream said HTTP 403 in body"}'),
            "gather_fail")
        # the true auth statuses still classify as auth
        self.assertEqual(
            mod.classify_fetch(
                1, '{"ok":false,"error":"GET /p failed with HTTP 401: nope"}'),
            "auth")
        self.assertEqual(
            mod.classify_fetch(
                1, '{"ok":false,"error":"GET /p failed with HTTP 403: nope"}'),
            "auth")

    def test_prepare_exit_3_writes_no_outputs(self):
        rc, so, se, run_dir, log = self._auth_run(401)
        self.assertEqual(rc, 3)
        # the fetch child failed on its first GET: nothing landed
        self.assertEqual(os.listdir(run_dir), [])
        self.assertFalse(os.path.exists(os.path.join(run_dir, "phases.jsonl")))


class PrepareGatherTests(unittest.TestCase):
    def _run_ok(self, extra=None):
        base, log = make_http_server(self, default_routes())
        d = tmp_dir(self)
        run_dir = os.path.join(d, "run")
        argv = ["--project", PROJECT, "--iid", MR, "--review-id", "rev-9",
                "--run-dir", run_dir]
        if extra:
            argv += extra
        rc, so, se = run_prepare(argv, host=base)
        return rc, so, se, run_dir, log

    def test_prepare_ok_run_writes_gather_json_schema(self):
        rc, so, se, run_dir, log = self._run_ok()
        self.assertEqual(rc, 0, se)
        gather_path = os.path.join(run_dir, "gather.json")
        self.assertTrue(os.path.isfile(gather_path), os.listdir(run_dir))
        with open(gather_path, encoding="utf-8") as fh:
            g = json.load(fh)
        self.assertEqual(g["schema"], "omniforge-mr-gather/1")
        self.assertEqual(set(g), {"schema", "fetched_at", "project",
                                  "mr_iid", "diff_refs", "data",
                                  "discussions", "versions"})
        self.assertEqual(g["project"], PROJECT)
        self.assertEqual(g["mr_iid"], MR)
        self.assertEqual(g["diff_refs"]["head_sha"], "head1")
        self.assertEqual(g["data"]["files_changed"],
                         ["src/app.py", "src/net.py", "README.md",
                          "src/auth_check.py"])
        # consumed as-is: prepare's own loader accepts the byte-shape
        mod = load_prepare()
        self.assertEqual(mod.load_gather(gather_path), g)

    def test_prepare_one_http_pass_get_sequence(self):
        rc, so, se, run_dir, log = self._run_ok()
        self.assertEqual(rc, 0, se)
        # exactly 6 GETs — one per endpoint, single page each, no re-fetch
        self.assertEqual(len(log), 6, log)
        def count(sub):
            return sum(1 for _, p in log if sub in p)
        self.assertEqual(count("/diffs"), 1)
        self.assertEqual(count("/discussions"), 1)
        self.assertEqual(count("/commits"), 1)
        self.assertEqual(count("/versions"), 1)
        self.assertEqual(count("/notes"), 1)
        self.assertEqual(count("/merge_requests/" + MR), 6)  # prefix of all
        self.assertTrue(all(m == "GET" for m, _ in log))

    def test_prepare_5xx_exhausted_exit_1_stage_gather(self):
        # /diffs 500s: the fetch child burns its per-call retries plus the
        # single page retry (real sleeps, ~12 s) then exits 1 — a
        # non-auth gather failure maps to exit 1, stage "gather".
        base, log = make_http_server(self, [
            ("/diffs", 500, {"error": "boom"}),
            ("/merge_requests/" + MR, 200, META),
        ])
        d = tmp_dir(self)
        run_dir = os.path.join(d, "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "rev-9",
             "--run-dir", run_dir], host=base)
        self.assertEqual(rc, 1)
        st = stdout_json(so)
        self.assertIs(st["ok"], False)
        self.assertEqual(st["error"], "prepare_failed")
        self.assertEqual(st["stage"], "gather")
        self.assertIn("detail", st)
        self.assertIn("HTTP 500", st["detail"])
        # one diagnostic stderr line
        diag = [l for l in se.splitlines() if l.strip()]
        self.assertEqual(len(diag), 1, se)
        self.assertIn("omni_prepare", diag[0])
        # no outputs landed (fetch child failed before writing)
        self.assertFalse(os.path.exists(os.path.join(run_dir, "gather.json")))

    def test_prepare_classify_fetch_exit_2_to_stage_gather(self):
        # fetcher exit 2 (non-token precondition) is a gather failure, not auth
        mod = load_prepare()
        self.assertEqual(mod.classify_fetch(2, ""), "gather_fail")


class PrepareVerifyHeadTests(unittest.TestCase):
    def _run(self, verify):
        base, log = make_http_server(self, default_routes())
        d = tmp_dir(self)
        run_dir = os.path.join(d, "run")
        argv = ["--project", PROJECT, "--iid", MR, "--review-id", "rev-9",
                "--run-dir", run_dir, "--verify-head", verify]
        rc, so, se = run_prepare(argv, host=base)
        return rc, so, se, run_dir

    def test_prepare_verify_head_moved_exit_4_exact_stdout(self):
        rc, so, se, run_dir = self._run("oldsha9")
        self.assertEqual(rc, 4)
        self.assertEqual(stdout_json(so), {
            "ok": False, "head_moved": True,
            "recorded_head": "oldsha9", "current_head": "head1"})
        # exactly one stderr line
        diag = [l for l in se.splitlines() if l.strip()]
        self.assertEqual(len(diag), 1, se)
        # gather.json may exist (full gather already ran)...
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "gather.json")))
        # ...but nothing downstream is written
        self.assertFalse(os.path.exists(
            os.path.join(run_dir, "partition.json")))
        self.assertFalse(os.path.exists(
            os.path.join(run_dir, "prepare.json")))
        self.assertFalse(os.path.exists(os.path.join(run_dir, "briefs")))
        self.assertFalse(os.path.exists(
            os.path.join(run_dir, "phases.jsonl")))

    def test_prepare_verify_head_stable_proceeds(self):
        rc, so, se, run_dir = self._run("head1")
        self.assertEqual(rc, 0, se)
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "gather.json")))

    def test_prepare_exit_4_writes_no_outputs_but_gather(self):
        d = tmp_dir(self)
        run_dir = os.path.join(d, "run")
        os.makedirs(run_dir)
        phases = os.path.join(run_dir, "phases.jsonl")
        with open(phases, "w", encoding="utf-8") as fh:
            fh.write('{"phase":"prepare","review_id":"older"}\n')
        base, log = make_http_server(self, default_routes())
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "rev-9",
             "--run-dir", run_dir, "--verify-head", "oldsha9"], host=base)
        self.assertEqual(rc, 4)
        # STOP before partition/briefs/prepare.json/phases append: the
        # journal line count is unchanged
        with open(phases, encoding="utf-8") as fh:
            lines = [l for l in fh.read().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(json.loads(lines[0])["review_id"], "older")
        self.assertFalse(os.path.exists(os.path.join(run_dir, "prepare.json")))


class PreparePartitionTests(unittest.TestCase):
    def test_prepare_partition_json_persisted_shape(self):
        # the REAL omni_partition.py subprocess runs over the run-dir
        # gather.json (it reads the gather-file shape natively) and its
        # partition.json lands in the run dir: files[] + agents{} with the
        # fixture's deterministic ownership (README.md docs->analyst,
        # src/app.py+src/net.py generic->codebase, src/auth_check.py
        # security-affinity->security; canonical order largest-first,
        # path-ascending ties)
        base, log = make_http_server(self, default_routes())
        d = tmp_dir(self)
        run_dir = os.path.join(d, "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "rev-9",
             "--run-dir", run_dir], host=base)
        self.assertEqual(rc, 0, se)
        partition_path = os.path.join(run_dir, "partition.json")
        self.assertTrue(os.path.isfile(partition_path), os.listdir(run_dir))
        with open(partition_path, encoding="utf-8") as fh:
            part = json.load(fh)
        self.assertEqual(set(part), {"files", "agents"})
        self.assertEqual([f["path"] for f in part["files"]],
                         ["README.md", "src/app.py", "src/auth_check.py",
                          "src/net.py"])
        for entry in part["files"]:
            self.assertEqual(set(entry),
                             {"path", "added_lines", "owner", "reason"})
        self.assertEqual(part["agents"]["analyst"]["files"], ["README.md"])
        self.assertEqual(part["agents"]["analyst"]["added_lines_total"], 3)
        self.assertEqual(part["agents"]["codebase"]["files"],
                         ["src/app.py", "src/net.py"])
        self.assertEqual(part["agents"]["codebase"]["added_lines_total"], 4)
        self.assertEqual(part["agents"]["security"]["files"],
                         ["src/auth_check.py"])
        self.assertEqual(part["agents"]["security"]["added_lines_total"], 2)

    def test_prepare_partition_subprocess_failure_maps_exit_1(self):
        mod = load_prepare()
        # (1) the seam itself: the REAL omni_partition.py subprocess on a
        # planted corrupt gather exits nonzero (diagnostic on its stderr,
        # stdout empty) and writes nothing
        d = tmp_dir(self)
        gather_path = os.path.join(d, "gather.json")
        with open(gather_path, "w", encoding="utf-8") as fh:
            fh.write("{corrupt")
        out_path = os.path.join(d, "partition.json")
        part_rc, part_stdout = mod.run_partition(gather_path, out_path)
        self.assertNotEqual(part_rc, 0)
        self.assertFalse(os.path.exists(out_path))
        # (2) the mapping: a nonzero partition rc inside a full run maps to
        # exit 1, stage "partition" (patched seam — the wipe of later slugs
        # must never be what makes this test pass or fail)
        base, log = make_http_server(self, default_routes())
        run_dir = os.path.join(d, "run")
        with mock.patch.object(mod, "run_partition",
                               return_value=(part_rc, part_stdout)):
            rc, so, se = run_prepare(
                ["--project", PROJECT, "--iid", MR, "--review-id", "rev-9",
                 "--run-dir", run_dir], host=base)
        self.assertEqual(rc, 1)
        st = stdout_json(so)
        self.assertIs(st["ok"], False)
        self.assertEqual(st["error"], "prepare_failed")
        self.assertEqual(st["stage"], "partition")
        self.assertIn("detail", st)
        diag = [l for l in se.splitlines() if l.strip()]
        self.assertEqual(len(diag), 1, se)
        self.assertIn("omni_prepare", diag[0])


if __name__ == "__main__":
    unittest.main()
