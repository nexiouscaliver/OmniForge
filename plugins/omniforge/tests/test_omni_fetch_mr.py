"""Contract tests for the one-shot MR gather (omni_fetch_mr.py, W4 T1).

The transport is mocked in-process — tests load the script via importlib and
monkeypatch omni_glab_api.request (the module-level seam) with a
contract-honoring fake, or omni_glab_api._http + sleep_fn for the real
retry-classification path. No network, no subprocess glab. Stdlib only —
no pytest import — so the module runs identically under bare unittest and
pytest.

Pinned contract (spec T1 / plan T1):
- gather mode: ONE invocation produces the omniforge-mr-gather/1 file whose
  data object is field-compatible with the v3.3.0 fetch_mr_data envelope
  (PLUS diff_refs) and whose embedded discussions envelope carries the
  fetch_mr_discussions item shape;
- endpoints: metadata, /diffs, /discussions, /commits (paginated
  per_page=100&page=N), /versions and /notes (first page) — exactly the
  expected GETs, no per-file re-fetch;
- retry: per-call bounded backoff on 5xx/429 (2 s then 4 s at defaults),
  fail-fast 404 naming method+path+status; a page exhausting its per-call
  retries gets exactly ONE additional page retry before exit 1;
- exit codes: 0 gather OK (one stdout JSON line with elapsed_ms + api_calls),
  1 API failure ({ok:false,error,endpoint}), 2 usage/token (no stdout JSON),
  4 verify-head moved ({ok:false,head_moved:true,recorded_head,current_head});
- atomic write: <out>.tmp then os.replace — a failed run leaves no partial
  output and no .tmp residue;
- --verify-head: ONE metadata GET, writes nothing, --out not required.
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
FETCH = os.path.join(SCRIPTS, "omni_fetch_mr.py")
TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools"))

sys.path.insert(0, SCRIPTS)
import omni_glab_api  # noqa: E402  (same module object the script imports)

PROJECT = "73279395"
MR = "21"

APP_DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -40,6 +40,9 @@ def handler(cfg):
     context line
+    if cfg is None:
+        return None
+    return cfg.get("x")
     more context
"""

NET_DIFF = """diff --git a/src/net.py b/src/net.py
index 3333333..4444444 100644
--- a/src/net.py
+++ b/src/net.py
@@ -10,4 +10,4 @@ def fetch(url):
     ctx
-    old_endpoint = "http://hardcoded"
+    new_endpoint = url
     tail
"""

DIFF_TEXT = APP_DIFF + NET_DIFF            # assembled diff (server order)

# Diff fixtures reused from tests/test_diff_mapping.py for the ported-function
# equivalence pin (test_diff_line_map_ported).
SINGLE_FILE_DIFF = """\
diff --git a/.gitlab-ci.yml b/.gitlab-ci.yml
index cd48b40..a1c70fc 100644
--- a/.gitlab-ci.yml
+++ b/.gitlab-ci.yml
@@ -1069,13 +1069,34 @@ deploy_to_ecs_staging:
       check_not_empty STAGING_DB_PASSWORD
       check_not_empty STAGING_DB_NAME
       check_not_empty STAGING_DB_ENABLED
+      # Stripe Configuration (staging)
+      check_not_empty STRIPE_SECRET_KEY_STAGING
+      check_not_empty STRIPE_PUBLISHABLE_KEY_STAGING
       if [ "$validation_failed" = true ]; then
-        printf "OLD ERROR MESSAGE"
+        printf "NEW ERROR MESSAGE"
       fi
"""

MULTI_FILE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index aaa..bbb 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,6 +10,8 @@ def main():
     print("hello")
+    validate_input()
+    check_auth()
     return True
diff --git a/src/utils.py b/src/utils.py
index ccc..ddd 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -1,3 +1,5 @@
+import os
+import sys
 def helper():
     pass
@@ -20,4 +22,5 @@ def other():
     x = 1
+    y = 2
     return x
"""

DELETE_ONLY_DIFF = """\
diff --git a/old.py b/old.py
index aaa..bbb 100644
--- a/old.py
+++ b/old.py
@@ -5,7 +5,5 @@ def func():
     a = 1
-    b = 2
-    c = 3
     return a
"""

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

DIFF_PAGE = [
    {"old_path": "src/app.py", "new_path": "src/app.py", "diff": APP_DIFF},
    {"old_path": "src/net.py", "new_path": "src/net.py", "diff": NET_DIFF},
]

RAW_DISCUSSIONS = [
    {"id": "d1", "resolvable": True, "resolved": False,
     "notes": [
         {"system": False, "type": "DiffNote", "body": "please guard cfg",
          "author": {"username": "rev1"},
          "created_at": "2026-09-01T10:00:00Z",
          "position": {"new_path": "src/app.py", "new_line": 42,
                       "position_type": "text"}},
         {"system": False, "body": "agreed, will fix",
          "author": {"username": "dev"},
          "created_at": "2026-09-01T10:05:00Z"},
         {"system": True, "body": "changed the description",
          "author": {"username": "gitlab-bot"}},
     ]},
    {"id": "d2", "resolvable": False, "resolved": False,
     "notes": [
         {"system": False, "body": "general question",
          "author": {"username": "rev2"},
          "created_at": "2026-09-01T11:00:00Z"},
     ]},
]

RAW_COMMITS = [
    {"id": "c111", "title": "Add widget"},
    {"id": "c222", "title": "Fix lint"},
]

VERSIONS = [{"id": 1, "head_commit_sha": "head1", "created_at":
             "2026-09-01T09:00:00Z"}]

RAW_NOTES = [
    {"system": False, "body": "top-level note",
     "author": {"username": "rev3"},
     "created_at": "2026-09-01T12:00:00Z"},
    {"system": True, "body": "assigned to @dev",
     "author": {"username": "gitlab-bot"}},
]


def resp(obj):
    return {"status": 200, "body": json.dumps(obj), "json": obj}


def default_routes():
    # Order matters: substrings are matched most-specific first, the bare
    # MR path last (it is a prefix of every sub-endpoint path).
    return [
        ("/diffs", resp(DIFF_PAGE)),
        ("/discussions", resp(RAW_DISCUSSIONS)),
        ("/commits", resp(RAW_COMMITS)),
        ("/versions", resp(VERSIONS)),
        ("/notes", resp(RAW_NOTES)),
        ("/merge_requests/" + MR, resp(META)),
    ]


class FakeAPI:
    """Contract-honoring fake for omni_glab_api.request: records every call,
    serves scripted responses per path substring. A route value may be a
    single response (repeats) or a list consumed in order (pagination). A
    fail entry (substring, [outcomes]) raises/returns scripted outcomes
    before route matching; an Exception outcome propagates (simulating
    request()'s raise contract, e.g. exhausted retries or 4xx fail-fast)."""

    def __init__(self, routes=None, fail=None):
        self.routes = routes if routes is not None else default_routes()
        self.fail = fail or []
        self._per_route = {}
        self.calls = []

    def __call__(self, method, path, token, host=None, form=None,
                 attempts=3, backoff_base=2.0, sleep_fn=None):
        self.calls.append({"method": method, "path": path})
        for sub, outs in self.fail:
            if sub in path and outs:
                out = outs.pop(0)
                if isinstance(out, Exception):
                    raise out
                return out
        for i, (sub, r) in enumerate(self.routes):
            if sub in path:
                if isinstance(r, list):
                    queue = self._per_route.setdefault(i, list(r))
                    return queue.pop(0) if queue else r[-1]
                return r
        raise AssertionError("FakeAPI: no route for %s %s" % (method, path))

    def count(self, sub):
        return sum(1 for c in self.calls if sub in c["path"])


def load_fetch_module():
    spec = importlib.util.spec_from_file_location("omni_fetch_mr_under_test",
                                                  FETCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tmp_dir(testcase):
    d = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


def run_fetch(argv, api=None, http=None, sleeps=None, token="test-token-1"):
    """Run main() in-process with the transport seam(s) patched. token=None
    strips both token env vars (missing-token contract)."""
    mod = load_fetch_module()
    saved = (omni_glab_api.request, omni_glab_api._http,
             omni_glab_api.sleep_fn)
    if api is not None:
        omni_glab_api.request = api
    if http is not None:
        omni_glab_api._http = http
    if sleeps is not None:
        omni_glab_api.sleep_fn = sleeps.append
    env = {} if token is None else {"GITLAB_TOKEN": token}
    out, err = io.StringIO(), io.StringIO()
    try:
        with mock.patch.dict(os.environ, env, clear=False):
            for key in ("GITLAB_TOKEN", "OMNIFORGE_GITLAB_TOKEN",
                        "GITLAB_HOST", "CI_API_V4_URL"):
                if key not in env:
                    os.environ.pop(key, None)
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                    rc = mod.main([str(x) for x in argv])
    finally:
        (omni_glab_api.request, omni_glab_api._http,
         omni_glab_api.sleep_fn) = saved
    return rc, out.getvalue(), err.getvalue()


def stdout_json(text):
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) != 1:
        raise ValueError("expected exactly one stdout JSON line, got %d: %r" % (
            len(lines), text[:400]))
    return json.loads(lines[0])


ENVELOPE_FIELDS = {"success", "mr_id", "title", "author", "source_branch",
                   "target_branch", "pipeline_status", "description",
                   "comments", "diff", "diff_line_count", "diff_too_large",
                   "diff_truncated", "diff_line_map", "commits",
                   "files_changed", "labels", "assignees", "reviewers",
                   "diff_refs"}


class OmniFetchMrTests(unittest.TestCase):
    def test_gather_schema_and_envelope(self):
        d = tmp_dir(self)
        out = os.path.join(d, "gather.json")
        api = FakeAPI()
        rc, so, se = run_fetch(["--project", PROJECT, "--mr", MR, "--out", out],
                               api=api)
        self.assertEqual(rc, 0, se)
        with open(out, encoding="utf-8") as fh:
            g = json.load(fh)
        self.assertEqual(g["schema"], "omniforge-mr-gather/1")
        self.assertEqual(set(g), {"schema", "fetched_at", "project", "mr_iid",
                                  "diff_refs", "data", "discussions",
                                  "versions"})
        self.assertEqual(g["project"], PROJECT)
        self.assertEqual(g["mr_iid"], MR)
        self.assertIsInstance(g["fetched_at"], str)
        refs = {"base_sha": "b000", "head_sha": "head1", "start_sha": "s000"}
        self.assertEqual(g["diff_refs"], refs)
        # data = the v3.3.0 fetch_mr_data envelope PLUS diff_refs
        data = g["data"]
        self.assertTrue(ENVELOPE_FIELDS.issubset(data), sorted(data))
        self.assertIs(data["success"], True)
        self.assertEqual(data["mr_id"], MR)
        self.assertEqual(data["title"], "Add widget API")
        self.assertEqual(data["author"], "dev")
        self.assertEqual(data["source_branch"], "feat/widget")
        self.assertEqual(data["target_branch"], "main")
        self.assertEqual(data["pipeline_status"], "success")
        self.assertEqual(data["description"], "Adds the widget endpoint.")
        self.assertEqual(data["comments"], "top-level note")  # non-system
        self.assertEqual(data["diff"], DIFF_TEXT)
        self.assertEqual(data["diff_line_count"], DIFF_TEXT.count("\n"))
        self.assertIs(data["diff_too_large"], False)
        self.assertIs(data["diff_truncated"], False)
        self.assertEqual(sorted(data["diff_line_map"]),
                         ["src/app.py", "src/net.py"])
        self.assertEqual(data["commits"],
                         [{"sha": "c111", "message": "Add widget"},
                          {"sha": "c222", "message": "Fix lint"}])
        self.assertEqual(data["files_changed"], ["src/app.py", "src/net.py"])
        self.assertEqual(data["labels"], ["backend"])
        self.assertEqual(data["assignees"], ["a1"])
        self.assertEqual(data["reviewers"], ["r1"])
        self.assertEqual(data["diff_refs"], refs)
        # embedded discussions envelope: fetch_mr_discussions shape
        disc = g["discussions"]
        self.assertIs(disc["success"], True)
        self.assertEqual(disc["mr_id"], MR)
        self.assertEqual(disc["total"], 2)
        self.assertEqual(disc["unresolved"], 1)
        self.assertEqual(disc["resolved"], 0)
        items = disc["discussions"]
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertEqual(set(item),
                             {"id", "resolvable", "resolved", "type",
                              "file_path", "line_number", "body", "author",
                              "created_at", "replies"})
        d1, d2 = items
        self.assertEqual(d1["id"], "d1")
        self.assertEqual(d1["type"], "inline")
        self.assertEqual(d1["file_path"], "src/app.py")
        self.assertEqual(d1["line_number"], 42)
        self.assertEqual(d1["body"], "please guard cfg")
        self.assertEqual(d1["author"], "rev1")
        self.assertEqual(d1["replies"],
                         [{"author": "dev", "body": "agreed, will fix",
                           "created_at": "2026-09-01T10:05:00Z"}])
        self.assertEqual(d2["type"], "general")
        self.assertIsNone(d2["file_path"])
        self.assertIsNone(d2["line_number"])
        # versions ride raw
        self.assertEqual(g["versions"], VERSIONS)

    def test_gather_api_call_count(self):
        d = tmp_dir(self)
        out = os.path.join(d, "g.json")
        # single-page fixtures: 6 GETs total (one per endpoint)
        api = FakeAPI()
        rc, so, se = run_fetch(["--project", PROJECT, "--mr", MR, "--out", out],
                               api=api)
        self.assertEqual(rc, 0, se)
        self.assertEqual(len(api.calls), 6, api.calls)
        self.assertEqual(api.count("/diffs"), 1)
        self.assertEqual(api.count("/discussions"), 1)
        self.assertEqual(api.count("/commits"), 1)
        self.assertEqual(api.count("/versions"), 1)
        self.assertEqual(api.count("/notes"), 1)
        self.assertEqual(api.count("/merge_requests/" + MR), 6)  # prefix of all
        self.assertTrue(all(c["method"] == "GET" for c in api.calls))
        # 2-page diffs fixture: exactly one extra diffs GET, page=2 fetched,
        # nothing else changes — no per-file re-fetch
        api2 = FakeAPI(routes=[
            ("/diffs", [resp([{"diff": APP_DIFF}] * 100), resp(DIFF_PAGE)]),
            ("/discussions", resp(RAW_DISCUSSIONS)),
            ("/commits", resp(RAW_COMMITS)),
            ("/versions", resp(VERSIONS)),
            ("/notes", resp(RAW_NOTES)),
            ("/merge_requests/" + MR, resp(META)),
        ])
        out2 = os.path.join(d, "g2.json")
        rc2, so2, se2 = run_fetch(["--project", PROJECT, "--mr", MR,
                                   "--out", out2], api=api2)
        self.assertEqual(rc2, 0, se2)
        self.assertEqual(len(api2.calls), 7, api2.calls)
        self.assertEqual(api2.count("/diffs"), 2)
        self.assertTrue(any("page=2" in c["path"] for c in api2.calls))
        self.assertFalse(any("page=3" in c["path"] for c in api2.calls))
        self.assertEqual(api2.count("/discussions"), 1)
        st = stdout_json(so2)
        self.assertEqual(st["api_calls"], 7)

    def test_retry_on_5xx_then_success(self):
        # real request() retry classification: _http scripted 500-then-200 on
        # the diffs page, in-process sleep recorder, attempts=2
        d = tmp_dir(self)
        out = os.path.join(d, "g.json")
        calls = {"diffs": 0}

        def http(url, headers, data=None):
            if "/diffs" in url:
                calls["diffs"] += 1
                if calls["diffs"] == 1:
                    return 500, "server error"
                return 200, json.dumps(DIFF_PAGE)
            if "/discussions" in url:
                return 200, json.dumps(RAW_DISCUSSIONS)
            if "/commits" in url:
                return 200, json.dumps(RAW_COMMITS)
            if "/versions" in url:
                return 200, json.dumps(VERSIONS)
            if "/notes" in url:
                return 200, json.dumps(RAW_NOTES)
            return 200, json.dumps(META)

        sleeps = []
        rc, so, se = run_fetch(["--project", PROJECT, "--mr", MR, "--out", out,
                                "--attempts", "2"], http=http, sleeps=sleeps)
        self.assertEqual(rc, 0, se)
        self.assertEqual(sleeps, [2.0])      # one backoff before the retry
        self.assertTrue(os.path.isfile(out))

    def test_no_retry_on_404_fails_fast(self):
        err = omni_glab_api.GlabApiError(
            "GET", "/projects/%s/merge_requests/%s" % (PROJECT, MR), 404,
            "404 Not Found", retryable=False)
        api = FakeAPI(fail=[("/merge_requests/" + MR, [err])])
        d = tmp_dir(self)
        out = os.path.join(d, "g.json")
        rc, so, se = run_fetch(["--project", PROJECT, "--mr", MR, "--out", out],
                               api=api)
        self.assertEqual(rc, 1)
        st = stdout_json(so)
        self.assertIs(st["ok"], False)
        self.assertIn("error", st)
        self.assertIn("merge_requests/%s" % MR, st["endpoint"])
        self.assertEqual(len(api.calls), 1)          # fail-fast: one call
        self.assertIn("GET", se)
        self.assertIn("merge_requests/%s" % MR, se)
        self.assertIn("404", se)

    def test_pagination_failure_single_extra_retry(self):
        # every diffs-page request() call raises retryable exhaustion: the
        # page gets exactly ONE additional retry, then exit 1
        def exhausted():
            return omni_glab_api.GlabApiError(
                "GET", "/projects/%s/merge_requests/%s/diffs" % (PROJECT, MR),
                502, "Bad Gateway after retries", retryable=True)

        api = FakeAPI(fail=[("/diffs", [exhausted(), exhausted(),
                                        exhausted()])])
        d = tmp_dir(self)
        out = os.path.join(d, "g.json")
        rc, so, se = run_fetch(["--project", PROJECT, "--mr", MR, "--out", out],
                               api=api)
        self.assertEqual(rc, 1)
        self.assertEqual(api.count("/diffs"), 2,
                         "exactly one additional page retry expected")
        st = stdout_json(so)
        self.assertIs(st["ok"], False)

    def test_missing_token_exit_2_names_fix(self):
        d = tmp_dir(self)
        out = os.path.join(d, "g.json")
        rc, so, se = run_fetch(["--project", PROJECT, "--mr", MR, "--out", out],
                               api=FakeAPI(), token=None)
        self.assertEqual(rc, 2)
        self.assertIn("glab auth status", se)
        self.assertEqual(so.strip(), "")            # no stdout JSON
        self.assertFalse(os.path.exists(out))

    def test_atomic_write(self):
        # a failed run (fail-fast on a later endpoint) leaves no partial
        # --out and no .tmp residue
        err = omni_glab_api.GlabApiError(
            "GET", "/projects/%s/merge_requests/%s/diffs" % (PROJECT, MR),
            404, "404 Not Found", retryable=False)
        api = FakeAPI(fail=[("/diffs", [err])])
        d = tmp_dir(self)
        out = os.path.join(d, "gather.json")
        rc, so, se = run_fetch(["--project", PROJECT, "--mr", MR, "--out", out],
                               api=api)
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(out))
        self.assertFalse(os.path.exists(out + ".tmp"))
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])

    def test_diff_line_map_ported(self):
        mod = load_fetch_module()
        sys.path.insert(0, TOOLS)
        try:
            from omniforge_mcp_server import (
                parse_diff_line_map as srv_map,
                extract_changed_files as srv_files,
                truncate_diff_if_needed as srv_trunc)
        finally:
            sys.path.remove(TOOLS)
        for fixture in (SINGLE_FILE_DIFF, MULTI_FILE_DIFF, DELETE_ONLY_DIFF,
                        DIFF_TEXT):
            with self.subTest(fixture=fixture.splitlines()[0]):
                self.assertEqual(mod.parse_diff_line_map(fixture),
                                 srv_map(fixture))
                self.assertEqual(mod.extract_changed_files(fixture),
                                 srv_files(fixture))
                self.assertEqual(
                    mod.truncate_diff_if_needed(fixture,
                                                fixture.count("\n")),
                    srv_trunc(fixture, fixture.count("\n")))
        # --max-diff-lines knob: the ported truncation honors the ceiling
        big = "\n".join("+line %d" % i for i in range(50))
        text, truncated = mod.truncate_diff_if_needed(big, 50, max_lines=5)
        self.assertIs(truncated, True)
        self.assertIn("[TRUNCATED:", text)
        self.assertEqual(len([l for l in text.split("\n") if l]), 6)

    def test_stdout_json_receipts(self):
        d = tmp_dir(self)
        out = os.path.join(d, "gather.json")
        api = FakeAPI()
        rc, so, se = run_fetch(["--project", PROJECT, "--mr", MR, "--out", out],
                               api=api)
        self.assertEqual(rc, 0, se)
        st = stdout_json(so)
        self.assertTrue({"ok", "out", "project", "mr_iid", "head_sha",
                         "api_calls", "diff_line_count", "files_changed",
                         "discussions_total", "discussions_unresolved",
                         "versions", "elapsed_ms"}.issubset(st), sorted(st))
        self.assertIs(st["ok"], True)
        self.assertEqual(st["out"], out)
        self.assertEqual(st["project"], PROJECT)
        self.assertEqual(st["mr_iid"], MR)
        self.assertEqual(st["head_sha"], "head1")
        self.assertEqual(st["api_calls"], 6)
        self.assertEqual(st["diff_line_count"], DIFF_TEXT.count("\n"))
        self.assertEqual(st["files_changed"], 2)
        self.assertEqual(st["discussions_total"], 2)
        self.assertEqual(st["discussions_unresolved"], 1)
        self.assertEqual(st["versions"], 1)
        self.assertIsInstance(st["elapsed_ms"], int)
        self.assertGreaterEqual(st["elapsed_ms"], 0)


class VerifyHeadTests(unittest.TestCase):
    def test_verify_head_equal_proceeds(self):
        api = FakeAPI()
        rc, so, se = run_fetch(["--project", PROJECT, "--mr", MR,
                                "--verify-head", "head1"], api=api)
        self.assertEqual(rc, 0, se)
        st = stdout_json(so)
        self.assertIs(st["ok"], True)
        self.assertIs(st["head_moved"], False)
        self.assertEqual(st["recorded_head"], "head1")
        self.assertEqual(st["current_head"], "head1")
        self.assertIn("elapsed_ms", st)

    def test_verify_head_moved_exit_4(self):
        api = FakeAPI()
        rc, so, se = run_fetch(["--project", PROJECT, "--mr", MR,
                                "--verify-head", "oldsha"], api=api)
        self.assertEqual(rc, 4)
        st = stdout_json(so)
        self.assertEqual(st, {"ok": False, "head_moved": True,
                              "recorded_head": "oldsha",
                              "current_head": "head1"})

    def test_verify_head_one_get_only(self):
        d = tmp_dir(self)
        api = FakeAPI()
        rc, so, se = run_fetch(["--project", PROJECT, "--mr", MR,
                                "--verify-head", "head1",
                                # --out intentionally omitted: not required
                                ], api=api)
        self.assertEqual(rc, 0, se)
        self.assertEqual(len(api.calls), 1, api.calls)
        self.assertEqual(api.calls[0]["method"], "GET")
        self.assertEqual(api.calls[0]["path"],
                         "/projects/%s/merge_requests/%s" % (PROJECT, MR))
        self.assertEqual(os.listdir(d), [])          # nothing written


if __name__ == "__main__":
    unittest.main()
