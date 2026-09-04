"""Contract tests for the direct-API GitLab MR review poster
(omni_post_review.py, W4 T2 rewrite).

The poster module is loaded IN-PROCESS via importlib (the existing
load_poster_module convention) and omni_glab_api.request is monkeypatched
with a fake honoring the same return/raise contract — bounded retry on
429/5xx with backoff, fail-fast GlabApiError on other 4xx — recording
every attempt as (method, path, form). The backoff-schedule test runs the
REAL transport with omni_glab_api._http scripted. Stdlib only — no pytest
import — so the module runs identically under bare unittest and pytest.

Pinned semantics (spec D6 through the W4 transport swap): per-call retry
ONLY on 5xx/429/network, exponential backoff base*2^(attempt-1) (2 s then
4 s at defaults), 400/401/403/404 fails fast naming method+path+status; ONE
summary note per invocation; one ANCHORED inline thread per findings entry
via POST .../discussions with the documented position[...] form keys (literal
bracket keys — the glab --raw-field nested-key loss class is gone) and diff
refs fetched ONCE per invocation (N+1 discipline); the duplicate-summary
guard refuses (exit 3) when an OmniForge summary note newer than --since
exists, is never evaluated for reply-only invocations, still evaluated for
--skip-summary unless --force, and is overridden by --force; --reply-to /
per-entry reply_to_thread_id route replies onto recorded threads and exclude
those entries from new-thread posting; --dry-run prints the exact calls,
executes nothing (zero transport calls) and works without a token; the
script never adds text (no AI attribution) to a body.

3.3.2 additions pinned here:
- NOTE entries — findings objects carrying ONLY {"body": ...} (no
  file_path/line_number) post as top-level MR notes (POST .../notes, form
  body=...) AFTER summary/threads/replies, counted in the additive stdout
  key "notes"; a body-only entry that also carries file_path or line_number
  is an ambiguous-shape usage error (exit 2, never silently skipped).
- AUTO-SKIP — --summary is required ONLY when the batch has at least one
  new-thread entry; reply-only/note-only batches run without a summary and
  without evaluating the guard when --summary is absent (implied skip);
  --skip-summary keeps its current meaning (guard still evaluated unless
  --force).
- --host threads through EVERY api call (same resolution order as the fetch
  script; recorded per call in FakeTransport.hosts).
- GUARD PAGINATION — the duplicate-summary guard lists notes via
  omni_glab_api.get_all (per_page=100, paginated): a summary on page 2 is
  still found (exit 3), while a single page still costs exactly ONE notes
  GET.
"""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import types
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest import mock

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
    "skills", "omnireview-gitlab", "scripts"))
POSTER = os.path.join(SCRIPTS, "omni_post_review.py")

sys.path.insert(0, SCRIPTS)
import omni_glab_api  # noqa: E402

PROJECT = "73279395"
MR = "136"

DIFF_REFS = {"iid": 136, "diff_refs": {
    "base_sha": "aaa111", "head_sha": "bbb222", "start_sha": "ccc333"}}

# created_at of the seeded OmniForge summary note in NOTES_WITH_SUMMARY.
SUMMARY_EPOCH = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc).timestamp()

NOTES_WITH_SUMMARY = [
    {"body": "Human comment", "created_at": "2026-08-01T09:00:00Z"},
    {"body": "## OmniForge\n\n**Verdict:** APPROVE_WITH_FIXES",
     "created_at": "2026-09-02T10:00:00Z"},
    {"body": "**Important** — inline note (not a summary)",
     "created_at": "2026-09-02T10:01:00Z"},
]

FINDINGS = [
    {"file_path": "src/app.py", "line_number": 42,
     "body": "**Important** — Missing null check\n\n**What:** cfg.get('x') "
             "dereferenced without a None guard.\n\nConfidence: 82/100 | "
             "Found by: Codebase Reviewer (OmniForge)"},
    {"file_path": "src/util.py", "line_number": 7,
     "body": "**Minor** — Magic number\n\n**What:** unexplained constant 7."
             "\n\nConfidence: 71/100 | Found by: Codebase Reviewer (OmniForge)"},
]


def refs_path(project=PROJECT, mr=MR):
    return "projects/%s/merge_requests/%s" % (project, mr)


def notes_list_path(project=PROJECT, mr=MR):
    # The guard's paginated notes listing, first page (get_all builds
    # per_page=100&page=N; page 1 is the exact single-page path).
    return refs_path(project, mr) + "/notes?per_page=100&page=1"


# The 3.3.1 guard path (no &page=1) — seeded too so both the pre- and
# post-pagination code shapes find the fixtures.
LEGACY_NOTES_PATH = refs_path() + "/notes?per_page=100"


def summary_path(project=PROJECT, mr=MR):
    return refs_path(project, mr) + "/notes"


def discussions_path(project=PROJECT, mr=MR):
    return refs_path(project, mr) + "/discussions"


def reply_path(thread_id, project=PROJECT, mr=MR):
    return refs_path(project, mr) + "/discussions/%s/notes" % thread_id


class FakeTransport:
    """Stand-in for omni_glab_api.request honoring the same contract:
    bounded retry (attempts) on 429/5xx with backoff_base * 2^(attempt-1)
    recorded into self.sleeps, fail-fast GlabApiError on other 4xx, and
    {"status","body","json"} on success. Every attempt is recorded in
    self.calls as (method, path, form). GET fixtures are served by exact
    path from self.gets; POST failure sequences by exact path from
    self.scripts (one status code consumed per attempt; exhausted or
    absent => success).
    """

    def __init__(self):
        self.calls = []
        self.hosts = []          # host arg per request (parallel to calls)
        self.gets = {}
        self.scripts = {}
        self.sleeps = []

    def script(self, path, codes):
        self.scripts[path] = list(codes)

    def _attempt_status(self, method, path):
        if method == "GET" and path in self.gets:
            value = self.gets[path]
            return 200, value
        codes = self.scripts.get(path)
        if codes:
            return codes.pop(0), None
        return 200, {}

    def request(self, method, path, token, host=None, form=None, attempts=3,
                backoff_base=2.0, sleep_fn=None):
        for attempt in range(1, attempts + 1):
            self.calls.append((method, path, list(form or [])))
            self.hosts.append(host)
            status, payload = self._attempt_status(method, path)
            if 200 <= status < 300:
                return {"status": status, "body": json.dumps(payload),
                        "json": payload}
            err = omni_glab_api.GlabApiError(
                method, path, status, "HTTP %d: server error body" % status,
                retryable=(status == 429 or status >= 500))
            if err.retryable and attempt < attempts:
                self.sleeps.append(backoff_base * (2 ** (attempt - 1)))
                continue
            raise err
        raise AssertionError("unreachable")


def write_file(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def load_poster_module():
    """In-process loader (pure-function + main() checks)."""
    spec = importlib.util.spec_from_file_location("omni_post_review_under_test",
                                                  POSTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_env(token="tok-test"):
    env = {k: v for k, v in os.environ.items()
           if k not in ("GITLAB_TOKEN", "OMNIFORGE_GITLAB_TOKEN",
                        "GITLAB_HOST", "CI_API_V4_URL")}
    if token is not None:
        env["GITLAB_TOKEN"] = token
    return env


class OmniPostReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.mod = load_poster_module()
        self.transport = FakeTransport()
        self.real_request = omni_glab_api.request   # pre-patch reference
        patcher = mock.patch.object(omni_glab_api, "request",
                                    self.transport.request)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.transport.gets[refs_path()] = DIFF_REFS
        self.transport.gets[notes_list_path()] = []
        self.transport.gets[LEGACY_NOTES_PATH] = []

    def set_notes(self, notes):
        self.transport.gets[notes_list_path()] = notes
        self.transport.gets[LEGACY_NOTES_PATH] = list(notes)

    def attempts(self, path, method=None):
        return [c for c in self.transport.calls
                if c[1] == path and (method is None or c[0] == method)]

    def guard_gets(self):
        """Every notes-listing GET the guard made (any page/query form)."""
        return [c for c in self.transport.calls
                if c[0] == "GET" and "/notes?per_page=100" in c[1]]

    def run_poster(self, findings, extra=(), token="tok-test",
                   summary="## OmniForge\n\n**Verdict:** APPROVE\n"):
        spath = write_file(os.path.join(self.tmp, "summary.md"), summary)
        fpath = write_file(os.path.join(self.tmp, "findings.json"),
                           json.dumps(findings))
        argv = ["--mr", MR, "--project", PROJECT,
                "--summary", spath, "--findings-json", fpath,
                "--backoff-base", "0"] + list(extra)
        return self.raw_poster(*argv, token=token)

    def raw_poster(self, *argv, token="tok-test"):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, run_env(token), clear=True), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                code = self.mod.main(list(argv))
            except SystemExit as e:              # argparse usage errors (2)
                code = e.code
        return types.SimpleNamespace(returncode=code, stdout=out.getvalue(),
                                     stderr=err.getvalue())

    def stdout_json(self, result):
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        return json.loads(lines[-1])

    # ── retry / fail-fast ─────────────────────────────────────

    def test_retry_on_5xx_then_success(self):
        self.transport.script(discussions_path(), [500])
        r = self.run_poster(FINDINGS)
        self.assertEqual(r.returncode, 0, r.stderr)
        # thread 1: attempt fails then succeeds (2 attempts); thread 2: 1
        self.assertEqual(len(self.attempts(discussions_path(), "POST")), 3)
        self.assertEqual(len(self.attempts(summary_path(), "POST")), 1)
        out = self.stdout_json(r)
        self.assertTrue(out["posted_summary"])
        self.assertEqual(out["threads"], 2)
        self.assertEqual(out["replies"], 0)
        self.assertEqual(out["failures"], 0)
        self.assertFalse(out["dry_run"])
        self.assertIn("elapsed_ms", out)

    def test_retry_on_429_then_success(self):
        self.transport.script(discussions_path(), [429])
        r = self.run_poster(FINDINGS)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.attempts(discussions_path(), "POST")), 3)
        self.assertEqual(self.stdout_json(r)["threads"], 2)

    def test_no_retry_on_400_fails_fast(self):
        self.transport.script(summary_path(), [400])
        r = self.run_poster(FINDINGS)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(self.attempts(summary_path(), "POST")), 1)
        self.assertEqual(len(self.attempts(discussions_path(), "POST")), 0)
        self.assertIn("400", r.stderr)
        self.assertIn("POST", r.stderr)             # error names the call
        self.assertIn(summary_path(), r.stderr)
        self.assertEqual(self.stdout_json(r)["failures"], 1)

    def test_backoff_schedule_2s_4s(self):
        # REAL transport (the setUp FakeTransport patch is reverted for
        # this test): script omni_glab_api._http 500,500,200 on the FORM
        # POSTs and record sleeps — pins the poster's default 2 s / 4 s
        # wiring through the actual retry loop.
        real_request = self.real_request
        scripted = [(500, "HTTP 500: boom"), (500, "HTTP 500: boom"),
                    (200, "{}")]
        state = {"n": 0}

        def fake_http(url, headers, data=None, method=None):
            if data is None:                        # GETs succeed at once
                return 200, json.dumps(DIFF_REFS)
            result = scripted[state["n"]]
            state["n"] += 1
            return result

        fpath = write_file(os.path.join(self.tmp, "findings.json"),
                           json.dumps(FINDINGS[:1]))
        sleeps = []
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, run_env(), clear=True), \
                redirect_stdout(out), redirect_stderr(err), \
                mock.patch.object(omni_glab_api, "request", real_request), \
                mock.patch.object(omni_glab_api, "_http", fake_http), \
                mock.patch.object(omni_glab_api, "sleep_fn", sleeps.append):
            code = self.mod.main(["--mr", MR, "--project", PROJECT,
                                  "--skip-summary", "--force",
                                  "--findings-json", fpath])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(sleeps, [2.0, 4.0])        # base * 2^(attempt-1)

    # ── findings shape + anchored position payload ─────────────

    def test_findings_json_shape_matches_posting_guide(self):
        findings = [
            {"file_path": "src/app.py", "line_number": 42,
             "body": "**Important** — thread one"},
            {"file_path": "src/app.py", "line_number": 87,
             "body": "**Minor** — thread two"},
            {"file_path": "src/util.py", "line_number": 7,
             "body": "**Minor** — thread three"},
        ]
        r = self.run_poster(findings)
        self.assertEqual(r.returncode, 0, r.stderr)
        posts = self.attempts(discussions_path(), "POST")
        self.assertEqual(len(posts), 3)             # one POST per entry
        for finding, (_, _, form) in zip(findings, posts):
            self.assertIn(("body", finding["body"]), form)
        self.assertEqual(self.stdout_json(r)["threads"], 3)

    def test_position_payload_keys_anchored(self):
        r = self.run_poster(FINDINGS[:1])
        self.assertEqual(r.returncode, 0, r.stderr)
        posts = self.attempts(discussions_path(), "POST")
        self.assertEqual(len(posts), 1)
        form = dict(posts[0][2])
        # ALL documented position[...] keys with real mock SHA values —
        # literal bracket keys, form-encoded (no glab flag layer to drop
        # them), so the thread anchors first-try.
        self.assertEqual(form["position[position_type]"], "text")
        self.assertEqual(form["position[base_sha]"], "aaa111")
        self.assertEqual(form["position[start_sha]"], "ccc333")
        self.assertEqual(form["position[head_sha]"], "bbb222")
        self.assertEqual(form["position[new_path]"], "src/app.py")
        self.assertEqual(form["position[old_path]"], "src/app.py")
        self.assertEqual(form["position[new_line]"], "42")
        self.assertNotIn("position[line_range]", form)  # cut from 3.3.1
        self.assertEqual(form["body"], FINDINGS[0]["body"])

    def test_position_old_line_variant(self):
        findings = [{"file_path": "src/new.py", "line_number": 42,
                     "old_path": "src/old.py", "old_line": 7,
                     "body": "**Minor** — deleted-file finding"}]
        r = self.run_poster(findings)
        self.assertEqual(r.returncode, 0, r.stderr)
        form = dict(self.attempts(discussions_path(), "POST")[0][2])
        self.assertEqual(form["position[new_path]"], "src/new.py")
        self.assertEqual(form["position[old_path]"], "src/old.py")
        self.assertEqual(form["position[old_line]"], "7")
        self.assertNotIn("position[new_line]", form)

    # ── dry-run executes nothing ──────────────────────────────

    def test_dry_run_executes_nothing(self):
        r = self.run_poster(FINDINGS, extra=["--dry-run"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.transport.calls, [])   # zero transport calls
        self.assertIn("DRY-RUN:", r.stdout)
        out = self.stdout_json(r)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["threads"], 2)

    def test_dry_run_position_placeholders(self):
        r = self.run_poster(FINDINGS[:1], extra=["--dry-run"])
        self.assertEqual(r.returncode, 0, r.stderr)
        chunks = r.stdout.split("DRY-RUN: ")[1:]
        inline = [c for c in chunks if discussions_path() in c]
        self.assertEqual(len(inline), 1)
        for token in ("<base_sha>", "<head_sha>", "<start_sha>",
                      "position[position_type]=text",
                      "position[new_path]=src/app.py"):
            self.assertIn(token, inline[0])

    # ── reply routing ─────────────────────────────────────────

    def test_reply_to_posts_on_recorded_thread(self):
        r = self.run_poster(FINDINGS[:1], extra=["--reply-to", "T1"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.attempts(reply_path("T1"), "POST")), 1)
        self.assertEqual(self.attempts(discussions_path(), "POST"), [])
        self.assertEqual(self.attempts(summary_path(), "POST"), [])
        out = self.stdout_json(r)
        self.assertEqual(out["replies"], 1)
        self.assertEqual(out["threads"], 0)
        self.assertFalse(out["posted_summary"])

    def test_reply_to_thread_id_entries_routed_as_replies(self):
        findings = [
            {"file_path": "src/app.py", "line_number": 42,
             "body": "**Important** — open prior",
             "reply_to_thread_id": "T7"},
            {"file_path": "src/util.py", "line_number": 7,
             "body": "**Minor** — new finding"},
        ]
        r = self.run_poster(findings)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.attempts(discussions_path(), "POST")), 1)
        self.assertEqual(len(self.attempts(reply_path("T7"), "POST")), 1)
        out = self.stdout_json(r)
        self.assertEqual(out["threads"], 1)          # reply entry excluded
        self.assertEqual(out["replies"], 1)
        self.assertTrue(out["posted_summary"])       # mixed batch: one summary

    # ── duplicate-summary guard ───────────────────────────────

    def test_duplicate_summary_guard(self):
        self.set_notes(NOTES_WITH_SUMMARY)
        r = self.run_poster(FINDINGS, extra=["--since", str(SUMMARY_EPOCH - 100)])
        self.assertEqual(r.returncode, 3)
        # refused: nothing posted (only the guard's notes-list GET ran)
        self.assertEqual(self.attempts(summary_path(), "POST"), [])
        self.assertEqual(self.attempts(discussions_path(), "POST"), [])
        self.assertIn("OmniForge", r.stderr)
        forced = self.run_poster(
            FINDINGS, extra=["--since", str(SUMMARY_EPOCH - 100), "--force"])
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertEqual(len(self.attempts(summary_path(), "POST")), 1)

    def test_summary_guard_ignores_same_run_summary(self):
        self.set_notes(NOTES_WITH_SUMMARY)
        r = self.run_poster(FINDINGS[:1], extra=["--reply-to", "T1"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.guard_gets(), [])      # guard never evaluated
        self.assertEqual(self.attempts(summary_path(), "POST"), [])
        self.assertEqual(len(self.attempts(reply_path("T1"), "POST")), 1)
        out = self.stdout_json(r)
        self.assertEqual(out["replies"], 1)
        self.assertFalse(out["posted_summary"])

    def test_guard_passes_when_note_older_than_since(self):
        self.set_notes(NOTES_WITH_SUMMARY)
        r = self.run_poster(FINDINGS, extra=["--since", str(SUMMARY_EPOCH + 100)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.attempts(summary_path(), "POST")), 1)

    # ── single summary note + N+1 refs discipline ─────────────

    def test_posts_exactly_one_summary_note(self):
        r = self.run_poster(FINDINGS)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.attempts(summary_path(), "POST")), 1)

    def test_diff_refs_fetched_once(self):
        findings = [{"file_path": "src/f%d.py" % i, "line_number": i + 1,
                     "body": "**Minor** — finding %d" % i} for i in range(5)]
        r = self.run_poster(findings)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.attempts(discussions_path(), "POST")), 5)
        self.assertEqual(len(self.attempts(refs_path(), "GET")), 1)

    # ── bodies stay caller-authored ───────────────────────────

    def test_no_ai_attribution_in_bodies(self):
        r = self.run_poster(FINDINGS)
        self.assertEqual(r.returncode, 0, r.stderr)
        posted = " ".join(repr(c[2]) for c in self.transport.calls)
        self.assertNotIn("Generated by", posted)
        self.assertNotIn("Claude", posted)

    # ── usage errors ──────────────────────────────────────────

    def test_usage_error_exit_2(self):
        with redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as cm:
                self.mod.main(["--mr", MR, "--project", PROJECT])  # missing
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(out.getvalue().strip(), "")  # no stdout JSON
        spath = write_file(os.path.join(self.tmp, "summary.md"),
                           "## OmniForge\n")
        bad = write_file(os.path.join(self.tmp, "bad.json"), "{not json")
        r2 = self.raw_poster("--mr", MR, "--project", PROJECT,
                             "--summary", spath, "--findings-json", bad)
        self.assertEqual(r2.returncode, 2)
        self.assertIn("findings", r2.stderr)
        self.assertEqual(r2.stdout.strip(), "")

    # ── --skip-summary ────────────────────────────────────────

    def test_skip_summary_posts_threads_only(self):
        r = self.run_poster(FINDINGS, extra=["--skip-summary"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.attempts(summary_path(), "POST"), [])
        # guard STILL evaluated (not reply-only, not --force): exactly ONE
        # notes GET when a single page suffices — pinned across the 3.3.2
        # pagination change (path form may vary, count may not).
        self.assertEqual(len(self.guard_gets()), 1)
        self.assertEqual(len(self.attempts(discussions_path(), "POST")), 2)
        out = self.stdout_json(r)
        self.assertFalse(out["posted_summary"])
        self.assertEqual(out["threads"], 2)

    def test_skip_summary_plus_reply_to_usage_error(self):
        r = self.run_poster(FINDINGS[:1],
                            extra=["--skip-summary", "--reply-to", "T1"])
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(self.transport.calls, [])

    def test_skip_summary_without_summary_file_is_valid(self):
        fpath = write_file(os.path.join(self.tmp, "findings.json"),
                           json.dumps(FINDINGS[:1]))
        r = self.raw_poster("--mr", MR, "--project", PROJECT,
                            "--skip-summary", "--findings-json", fpath,
                            "--backoff-base", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.attempts(discussions_path(), "POST")), 1)

    # ── missing summary without --skip-summary is a usage error ──

    def test_missing_summary_without_skip_is_usage_error(self):
        fpath = write_file(os.path.join(self.tmp, "findings.json"),
                           json.dumps(FINDINGS[:1]))
        r = self.raw_poster("--mr", MR, "--project", PROJECT,
                            "--findings-json", fpath)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--summary", r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    # ── missing token on real runs (dry-run works without one) ──

    def test_dry_run_works_without_token(self):
        r = self.run_poster(FINDINGS, extra=["--dry-run"], token=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.transport.calls, [])
        self.assertTrue(self.stdout_json(r)["dry_run"])
        # a REAL run without a token is a usage error naming the fix
        r2 = self.run_poster(FINDINGS, token=None)
        self.assertEqual(r2.returncode, 2)
        self.assertIn("glab auth status", r2.stderr)
        self.assertEqual(r2.stdout.strip(), "")
        self.assertEqual(self.transport.calls, [])

    # ── dry-run mirrors real-run guard behavior ───────────────

    def test_dry_run_force_skips_guard_command(self):
        # A real --force run never lists notes; dry-run must match.
        r = self.run_poster(FINDINGS, extra=["--dry-run", "--force"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("notes?per_page=100", r.stdout)
        r2 = self.run_poster(FINDINGS, extra=["--dry-run"])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("notes?per_page=100", r2.stdout)

    # ── 3.3.2: note entries (body-only findings -> top-level notes) ──

    def test_note_entries_post_as_top_level_notes(self):
        findings = [{"body": "**Minor** — MR-meta note, no diff locus"}]
        fpath = write_file(os.path.join(self.tmp, "findings.json"),
                           json.dumps(findings))
        # note-only batch: no --summary and no --skip-summary — the implied
        # skip takes over (3.3.2) instead of a usage error.
        r = self.raw_poster("--mr", MR, "--project", PROJECT,
                            "--findings-json", fpath, "--backoff-base", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.attempts(discussions_path(), "POST"), [])
        self.assertEqual(self.attempts(refs_path(), "GET"), [])
        self.assertEqual(self.guard_gets(), [])       # implied skip: no guard
        notes_posts = self.attempts(summary_path(), "POST")
        self.assertEqual(len(notes_posts), 1)         # the note, no summary
        self.assertEqual(notes_posts[0][2], [("body", findings[0]["body"])])
        out = self.stdout_json(r)
        self.assertFalse(out["posted_summary"])
        self.assertEqual(out["threads"], 0)
        self.assertEqual(out["replies"], 0)
        self.assertEqual(out["notes"], 1)             # additive stdout key
        self.assertEqual(out["failures"], 0)

    def test_mixed_batch_summary_thread_reply_note_counts(self):
        findings = [
            {"file_path": "src/app.py", "line_number": 42,
             "body": "THREAD-BODY"},
            {"body": "REPLY-BODY", "reply_to_thread_id": "T7"},
            {"body": "NOTE-BODY"},
        ]
        r = self.run_poster(findings)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.attempts(discussions_path(), "POST")), 1)
        self.assertEqual(len(self.attempts(reply_path("T7"), "POST")), 1)
        # summary AND the note share the .../notes endpoint: 2 POSTs
        notes_posts = self.attempts(summary_path(), "POST")
        self.assertEqual(len(notes_posts), 2)
        self.assertEqual(notes_posts[1][2], [("body", "NOTE-BODY")])
        # execution order: summary -> threads -> replies -> notes (last)
        calls = self.transport.calls

        def first_idx(pred):
            return next(i for i, c in enumerate(calls) if pred(c))

        i_summary = first_idx(lambda c: c[0] == "POST"
                              and c[1] == summary_path())
        i_thread = first_idx(lambda c: c[0] == "POST"
                             and c[1] == discussions_path())
        i_reply = first_idx(lambda c: c[0] == "POST"
                            and c[1] == reply_path("T7"))
        i_note = max(i for i, c in enumerate(calls) if c[0] == "POST"
                     and c[1] == summary_path())
        self.assertLess(i_summary, i_thread)
        self.assertLess(i_thread, i_reply)
        self.assertLess(i_reply, i_note)
        out = self.stdout_json(r)
        self.assertTrue(out["posted_summary"])
        self.assertEqual(out["threads"], 1)
        self.assertEqual(out["replies"], 1)
        self.assertEqual(out["notes"], 1)

    def test_note_entry_with_anchor_keys_is_usage_error(self):
        # line_number without file_path = ambiguous shape: exit 2, never a
        # silently-skipped or silently-threaded entry
        bad = write_file(os.path.join(self.tmp, "bad1.json"),
                         json.dumps([{"body": "x", "line_number": 42}]))
        r = self.raw_poster("--mr", MR, "--project", PROJECT,
                            "--findings-json", bad)
        self.assertEqual(r.returncode, 2)
        self.assertIn("ambiguous", r.stderr)
        self.assertEqual(r.stdout.strip(), "")
        # file_path without line_number: same refusal (thread shape needs both)
        bad2 = write_file(os.path.join(self.tmp, "bad2.json"),
                          json.dumps([{"body": "x",
                                       "file_path": "src/app.py"}]))
        r2 = self.raw_poster("--mr", MR, "--project", PROJECT,
                             "--findings-json", bad2)
        self.assertEqual(r2.returncode, 2)
        self.assertEqual(r2.stdout.strip(), "")
        self.assertEqual(self.transport.calls, [])

    def test_dry_run_note_path(self):
        findings = [{"body": "NOTE-BODY"},
                    {"file_path": "src/app.py", "line_number": 3,
                     "body": "THREAD-BODY"}]
        spath = write_file(os.path.join(self.tmp, "summary.md"),
                           "## OmniForge\n")
        fpath = write_file(os.path.join(self.tmp, "findings.json"),
                           json.dumps(findings))
        r = self.raw_poster("--mr", MR, "--project", PROJECT,
                            "--summary", spath, "--findings-json", fpath,
                            "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.transport.calls, [])   # executes nothing
        self.assertIn("DRY-RUN: POST %s body=NOTE-BODY" % summary_path(),
                      r.stdout)
        out = self.stdout_json(r)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["threads"], 1)
        self.assertEqual(out["notes"], 1)

    # ── 3.3.2: auto-summary-skip for no-new-thread batches ─────

    def test_reply_only_batch_without_summary_auto_skips(self):
        findings = [{"body": "REPLY-ONLY-BODY",
                     "reply_to_thread_id": "T9"}]
        fpath = write_file(os.path.join(self.tmp, "findings.json"),
                           json.dumps(findings))
        # production friction this fixes: reply-only batch without
        # --summary/--skip-summary used to die with argparse exit 2
        r = self.raw_poster("--mr", MR, "--project", PROJECT,
                            "--findings-json", fpath, "--backoff-base", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.attempts(summary_path(), "POST"), [])
        self.assertEqual(len(self.attempts(reply_path("T9"), "POST")), 1)
        self.assertEqual(self.guard_gets(), [])       # guard not evaluated
        out = self.stdout_json(r)
        self.assertFalse(out["posted_summary"])
        self.assertEqual(out["replies"], 1)
        self.assertEqual(out["notes"], 0)

    # ── 3.3.2: --host reaches every request ────────────────────

    def test_host_flag_reaches_every_request(self):
        findings = [
            {"file_path": "src/app.py", "line_number": 42,
             "body": "THREAD-BODY"},
            {"body": "REPLY-BODY", "reply_to_thread_id": "T7"},
            {"body": "NOTE-BODY"},
        ]
        r = self.run_poster(findings,
                            extra=["--host", "https://glab.example.test"])
        self.assertEqual(r.returncode, 0, r.stderr)
        # guard GET + refs GET + summary POST + thread POST + reply POST +
        # note POST — every single call carries the --host value
        self.assertEqual(len(self.transport.calls), 6, self.transport.calls)
        self.assertEqual(len(self.transport.hosts), len(self.transport.calls))
        self.assertEqual(set(self.transport.hosts),
                         {"https://glab.example.test"})

    # ── 3.3.2: duplicate-summary guard paginates ───────────────

    def test_guard_finds_duplicate_summary_on_page_2(self):
        # a busy MR can push the OmniForge summary past page 1: 100 filler
        # notes on page 1, the summary on page 2 — the guard must paginate
        # (get_all) and still refuse
        self.transport.gets[notes_list_path()] = [
            {"body": "filler %d" % i, "created_at": "2026-09-02T09:00:00Z"}
            for i in range(100)]
        self.transport.gets[refs_path() +
                            "/notes?per_page=100&page=2"] = NOTES_WITH_SUMMARY
        r = self.run_poster(
            FINDINGS, extra=["--since", str(SUMMARY_EPOCH - 100)])
        self.assertEqual(r.returncode, 3)
        # refused: nothing posted (only the guard's two notes-list GETs ran)
        self.assertEqual(self.attempts(summary_path(), "POST"), [])
        self.assertEqual(self.attempts(discussions_path(), "POST"), [])
        self.assertEqual(len(self.guard_gets()), 2)
        self.assertIn("OmniForge", r.stderr)


if __name__ == "__main__":
    unittest.main()
