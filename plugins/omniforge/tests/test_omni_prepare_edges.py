"""Edge-case contract tests for omni_prepare.py (round2-pre-dispatch T2).

Slug `prepare-edges` (plan section 3): the deferred R3a deleted-files-only
wording, the fatal prior-report exit-2 mapping + the valid prior-report
plumbing, and full-run regression pins for R3b zero-added-line files, the
blank empty diff, the >500-file cap, and binary (0-added) entries.

Slug `prepare-hardening` (T1 code-review carry-forwards): write_atomic .tmp
cleanup on a failed write, fault-injection pins for the uncovered exit
branches, subprocess timeouts (TimeoutExpired -> exit 1) with
errors="replace" text reads, the ownerless-agent brief marker, and the
load_gather shape-error message.

Harness conventions: copied from test_omni_prepare.py (spec section 9 — no
cross-module test import): stdlib unittest, offline, importlib script
loading, a silent ThreadingHTTPServer on 127.0.0.1 with daemon threads,
addCleanup shutdown/close, a routes table ordered most-specific-first (the
bare /merge_requests/{mr} path LAST — substring of every sub-endpoint).
Extension: a route body may be a CALLABLE taking the full request path
(including query string) and returning the JSON body — used for paginated
/diffs fixtures (501 files) where a static body would loop forever.
HTTP route fixtures are INLINE dicts (the on-disk fixtures dir belongs to
T1's golden tests only).
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

PROJECT = "73279395"
MR = "21"

# Empty-side owned-list wordings (plan section 2c, byte-exact).
EMPTY_DIFF_LINE = "(none — this MR has an empty diff)"
NO_ADDED_SIDE_LINE = "(none — no added-side files — deep-dive partitions are empty)"
NO_OWNED_LINE = "(none — no owned files)"

_PREPARE = None


def load_prepare():
    """Load omni_prepare.py under a stable unique name (cached per process
    so in-process monkeypatching of one test reaches main())."""
    global _PREPARE
    if _PREPARE is None:
        spec = importlib.util.spec_from_file_location(
            "omni_prepare_edges_under_test", PREPARE)
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
    test_omni_prepare.run_prepare. token=None strips both token env vars.
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


def make_http_server(testcase, routes):
    """Start a silent fake GitLab API on 127.0.0.1:<ephemeral>.

    Returns (base_url, requests_log). The log collects ("GET", path) for
    every request, including query strings, so one-pass GET sequences are
    assertable across the fetch grandchild subprocess. A route body may be
    a callable taking the full request path (query included) and returning
    the JSON body — the pagination seam for large inline /diffs fixtures.
    """
    requests_log = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests_log.append(("GET", self.path))
            for sub, status, body in routes:
                if sub in self.path:
                    if callable(body):
                        body = body(self.path)
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


# ── inline HTTP fixtures ──────────────────────────────────────────────────

META = {
    "iid": 21,
    "title": "Harden widget API",
    "description": "Adjusts the widget endpoint.",
    "author": {"username": "dev"},
    "source_branch": "feat/widget",
    "target_branch": "main",
    "head_pipeline": {"status": "success"},
    "diff_refs": {"base_sha": "b000", "head_sha": "head1", "start_sha": "s000"},
    "labels": ["backend"],
    "assignees": [{"username": "a1"}],
    "reviewers": [{"username": "r1"}],
}

# The /diffs API returns per-file diffs starting directly at the @@ hunks;
# the fetcher synthesizes the unified-diff headers (deleted_file items get
# +++ /dev/null and so never enter files_changed).
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
    {"id": "c111", "title": "Harden widget"},
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


# R3a fixture: EVERY /diffs item deletes a file (deleted_file: True -> the
# fetcher synthesizes +++ /dev/null -> files_changed == [] while the
# assembled diff is NON-blank).
DELETION_PAGE = [
    {"old_path": "legacy/old_mod.py", "new_path": "legacy/old_mod.py",
     "deleted_file": True,
     "diff": "@@ -1,3 +0,0 @@\n-import foo\n-from bar import baz\n"
             "-OLD_CONSTANT = 1\n"},
    {"old_path": "docs/old_guide.md", "new_path": "docs/old_guide.md",
     "deleted_file": True,
     "diff": "@@ -1,2 +0,0 @@\n-# Old Guide\n-Deprecated.\n"},
]

# R3b fixture: legacy.py is a MODIFIED file (+++ b/legacy.py synthesized)
# whose hunk carries only '-' lines — present in files_changed with
# added_lines 0. src/app.py (3 added) keeps the MR non-degenerate.
ZERO_ADDED_PAGE = [
    {"old_path": "src/app.py", "new_path": "src/app.py", "diff": APP_HUNK},
    {"old_path": "legacy.py", "new_path": "legacy.py",
     "diff": "@@ -5,3 +5,2 @@ def f(x):\n     ctx\n-    old_call()\n"
             "     return x\n"},
]

# Binary fixture: a binary asset whose /diffs body is a plain-text notice
# — the file still enters files_changed with 0 added lines and its diff
# body must never leak into any brief.
BINARY_PAGE = [
    {"old_path": "assets/logo.png", "new_path": "assets/logo.png",
     "diff": "Binary file logo.png differs\n"},
    {"old_path": "src/app.py", "new_path": "src/app.py", "diff": APP_HUNK},
    {"old_path": "README.md", "new_path": "README.md", "diff": README_HUNK},
    {"old_path": "src/auth_check.py", "new_path": "src/auth_check.py",
     "diff": AUTH_HUNK},
]

# >500-file cap fixture: 501 deterministic one-line files served through a
# page-aware callable route (per_page=100; a static body would loop).
CAP_N = 501
CAP_DIFF_ITEMS = [
    {"old_path": "src/f%03d.py" % i, "new_path": "src/f%03d.py" % i,
     "diff": "@@ -1,2 +1,3 @@\n ctx\n+new\n"}
    for i in range(CAP_N)
]


def paged_diffs_items(path):
    """Slice CAP_DIFF_ITEMS at per_page=100 boundaries for ?page=N."""
    m = re.search(r"[?&]page=(\d+)", path)
    page = int(m.group(1)) if m else 1
    return CAP_DIFF_ITEMS[(page - 1) * 100:page * 100]


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def brief_path(run_dir, n):
    return os.path.join(run_dir, "briefs", "agent-%d.md" % n)


class PrepareEdgeCasesTests(unittest.TestCase):
    """Slug `prepare-edges` red: the R3a wording variant and the prior-report
    fatal exit-2 mapping / valid plumbing are UNIMPLEMENTED at T1's green
    tip (deliberately deferred); the R3b / empty-diff / cap / binary full
    runs are regression pins riding the same slug."""

    def _run(self, routes, extra=None, review_id="edge-1"):
        base, log = make_http_server(self, routes)
        run_dir = os.path.join(tmp_dir(self), "run")
        argv = ["--project", PROJECT, "--iid", MR, "--review-id", review_id,
                "--run-dir", run_dir]
        if extra:
            argv += extra
        rc, so, se = run_prepare(argv, host=base)
        return rc, so, se, run_dir, log

    # ── R3a: deleted-files-only MR (genuinely red at T1's tip) ──────────

    def test_prepare_deletion_only_mr(self):
        rc, so, se, run_dir, log = self._run([
            ("/diffs", 200, DELETION_PAGE),
            ("/discussions", 200, RAW_DISCUSSIONS),
            ("/commits", 200, RAW_COMMITS),
            ("/versions", 200, VERSIONS),
            ("/notes", 200, RAW_NOTES),
            ("/merge_requests/" + MR, 200, META),
        ])
        self.assertEqual(rc, 0, se)
        doc = read_json(os.path.join(run_dir, "prepare.json"))
        self.assertEqual(doc["files"], 0)
        self.assertEqual(doc["added_lines"], 0)
        self.assertEqual(doc["partitions"], {
            "analyst": {"files": 0, "added_lines_total": 0},
            "codebase": {"files": 0, "added_lines_total": 0},
            "security": {"files": 0, "added_lines_total": 0}})
        self.assertEqual(stdout_json(so)["files"], 0)
        self.assertEqual(stdout_json(so)["partitions"],
                         {"analyst": 0, "codebase": 0, "security": 0})
        for n in (1, 2, 3):
            with open(brief_path(run_dir, n), encoding="utf-8") as fh:
                brief = fh.read()
            with self.subTest(agent=n):
                # non-blank diff + no added-side files -> the R3a neutral
                # line, NOT the empty-diff line (distinct fixtures, plan R3)
                self.assertIn(NO_ADDED_SIDE_LINE, brief)
                self.assertNotIn(EMPTY_DIFF_LINE, brief)

    # ── R3b + regression pins ────────────────────────────────────────────

    def test_prepare_zero_added_lines_files_render_zero(self):
        # legacy.py: modified (+++ b/ legacy.py), only '-' hunk lines ->
        # PRESENT in files_changed, 0 added lines, owned with the explicit
        # "0 added lines" bullet (greedy sends it to analyst: codebase took
        # src/app.py first).
        rc, so, se, run_dir, log = self._run([
            ("/diffs", 200, ZERO_ADDED_PAGE),
            ("/discussions", 200, []),
            ("/commits", 200, RAW_COMMITS),
            ("/versions", 200, VERSIONS),
            ("/notes", 200, []),
            ("/merge_requests/" + MR, 200, META),
        ])
        self.assertEqual(rc, 0, se)
        doc = read_json(os.path.join(run_dir, "prepare.json"))
        self.assertEqual(doc["files"], 2)
        self.assertEqual(doc["added_lines"], 3)
        with open(brief_path(run_dir, 1), encoding="utf-8") as fh:
            analyst = fh.read()
        self.assertIn("- `legacy.py` — 0 added lines — greedy-balance",
                      analyst)
        self.assertIn("- Owned: 1 files / 0 added lines", analyst)
        self.assertIn("- MR total: 2 files / 3 added lines", analyst)

    def test_prepare_empty_diff_exit0_files0(self):
        # blank diff (empty /diffs page) -> the EMPTY-diff wording, not R3a
        rc, so, se, run_dir, log = self._run([
            ("/diffs", 200, []),
            ("/discussions", 200, []),
            ("/commits", 200, []),
            ("/versions", 200, []),
            ("/notes", 200, []),
            ("/merge_requests/" + MR, 200, META),
        ])
        self.assertEqual(rc, 0, se)
        doc = read_json(os.path.join(run_dir, "prepare.json"))
        self.assertEqual(doc["files"], 0)
        for n in (1, 2, 3):
            with open(brief_path(run_dir, n), encoding="utf-8") as fh:
                brief = fh.read()
            with self.subTest(agent=n):
                self.assertIn(EMPTY_DIFF_LINE, brief)
                self.assertNotIn(NO_ADDED_SIDE_LINE, brief)

    def test_prepare_briefs_500plus_cap_full_run(self):
        # inline-generated 501-file /diffs fixture -> all three briefs
        # cap-render (reason dropped, collapsed cross-cutting line) while
        # partition.json still carries all 501 entries
        rc, so, se, run_dir, log = self._run([
            ("/diffs", 200, paged_diffs_items),
            ("/discussions", 200, []),
            ("/commits", 200, []),
            ("/versions", 200, []),
            ("/notes", 200, []),
            ("/merge_requests/" + MR, 200, META),
        ])
        self.assertEqual(rc, 0, se)
        part = read_json(os.path.join(run_dir, "partition.json"))
        self.assertEqual(len(part["files"]), CAP_N)
        doc = read_json(os.path.join(run_dir, "prepare.json"))
        self.assertEqual(doc["files"], CAP_N)
        for n in (1, 2, 3):
            with open(brief_path(run_dir, n), encoding="utf-8") as fh:
                brief = fh.read()
            with self.subTest(agent=n):
                self.assertIn("## Cross-cutting files (all %d changed "
                              "files)" % CAP_N, brief)
                self.assertIn("Cross-cutting: all %d changed files (see "
                              "partition.json)" % CAP_N, brief)
                self.assertNotIn(" — greedy-balance", brief)
                # the two depth sentences stay unconditionally
                self.assertIn("Deep-dive owner: these files:\n", brief)
                self.assertIn("grep depth", brief)

    def test_prepare_briefs_binary_files_stats_only(self):
        # 0-added binary-ish entry: rendered as a normal owned bullet,
        # counted in stats, and its diff body never leaks into any brief
        rc, so, se, run_dir, log = self._run([
            ("/diffs", 200, BINARY_PAGE),
            ("/discussions", 200, []),
            ("/commits", 200, []),
            ("/versions", 200, []),
            ("/notes", 200, []),
            ("/merge_requests/" + MR, 200, META),
        ])
        self.assertEqual(rc, 0, se)
        # greedy: app.py(3) -> codebase; logo.png(0): codebase 3 <=
        # analyst 3 (README docs-prefer) -> codebase owns the binary too
        with open(brief_path(run_dir, 2), encoding="utf-8") as fh:
            codebase = fh.read()
        self.assertIn("- `assets/logo.png` — 0 added lines — greedy-balance",
                      codebase)
        self.assertIn("- Owned: 2 files / 3 added lines", codebase)
        self.assertIn("- MR total: 4 files / 8 added lines", codebase)
        for n in (1, 2, 3):
            with open(brief_path(run_dir, n), encoding="utf-8") as fh:
                brief = fh.read()
            with self.subTest(agent=n):
                self.assertNotIn("Binary file", brief)
                self.assertNotIn("@@", brief)

    # ── prior-report: valid plumbing (genuinely red at T1's tip) ────────

    def test_prepare_prior_report_valid_summary(self):
        d = tmp_dir(self)
        prior = os.path.join(d, "prior_findings.json")
        with open(prior, "w", encoding="utf-8") as fh:
            json.dump([{"id": 1}, {"id": 2}, {"id": 3}], fh)
        rc, so, se, run_dir, log = self._run(default_routes(),
                                             extra=["--prior-report", prior])
        self.assertEqual(rc, 0, se)
        doc = read_json(os.path.join(run_dir, "prepare.json"))
        self.assertEqual(doc["prior_report"],
                         {"path": prior, "findings_count": 3})
        for n in (1, 2, 3):
            with open(brief_path(run_dir, n), encoding="utf-8") as fh:
                brief = fh.read()
            with self.subTest(agent=n):
                self.assertIn("- Prior review findings: 3 — see prior "
                              "report", brief)
        self.assertEqual(stdout_json(so)["prior_findings"], 3)

    # ── prior-report: fatal exit-2 mapping (genuinely red at T1's tip) ──

    def _fatal_run(self, prior_path, reason):
        # live run (NOT dry-run) against the local server: at green the
        # fatal check fires BEFORE the fetch — zero GETs, empty stdout,
        # one stderr line omni_prepare: --prior-report <reason>
        base, log = make_http_server(self, default_routes())
        run_dir = os.path.join(tmp_dir(self), "run")
        rc, so, se = run_prepare(
            ["--project", PROJECT, "--iid", MR, "--review-id", "edge-2",
             "--run-dir", run_dir, "--prior-report", prior_path], host=base)
        self.assertEqual(rc, 2, "expected exit 2, stderr=%r" % se)
        self.assertEqual(so.strip(), "")   # no stdout JSON on exit 2
        diag = [l for l in se.splitlines() if l.strip()]
        self.assertEqual(diag, ["omni_prepare: --prior-report %s" % reason],
                         se)
        self.assertEqual(log, [])           # the check precedes any network
        return run_dir

    def test_prepare_prior_report_missing_file_exit_2(self):
        d = tmp_dir(self)
        self._fatal_run(os.path.join(d, "absent.json"), "file not found")
        # an unreadable path (a directory: open() raises IsADirectoryError,
        # an OSError) is the same usage-class failure with its own reason
        self._fatal_run(d, "unreadable")

    def test_prepare_prior_report_invalid_json_exit_2(self):
        d = tmp_dir(self)
        invalid = os.path.join(d, "invalid.json")
        with open(invalid, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self._fatal_run(invalid, "invalid JSON")

    def test_prepare_prior_report_unrecognized_shape_exit_2(self):
        d = tmp_dir(self)
        wrong = os.path.join(d, "wrong_shape.json")
        with open(wrong, "w", encoding="utf-8") as fh:
            json.dump({"no_findings": True}, fh)
        self._fatal_run(wrong, "unrecognized shape")


if __name__ == "__main__":
    unittest.main()
