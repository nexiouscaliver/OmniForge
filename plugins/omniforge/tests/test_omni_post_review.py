"""Contract tests for the MCP-free posting fallback (omni_post_review.py).

The poster is invoked as a SUBPROCESS against a fake `glab` shim on a
PATH-first temp dir (exit codes + the one-JSON-line stdout contract are
exercised in a real process; same convention as test_omni_wait.py /
test_omni_validate_findings.py); the backoff-schedule test loads the module
in-process via importlib and patches mod._exec / mod.sleep_fn. Stdlib only —
no pytest import — so the module runs identically under bare unittest and
pytest.

Pinned semantics (spec D6): per-call retry ONLY on 5xx/429/network stderr,
exponential backoff base*2^(attempt-1) (2 s then 4 s at defaults), 4xx fails
fast naming the command; ONE summary note per invocation; one inline thread
per findings entry via the posting-guide nested-position raw-field
workaround with diff refs fetched ONCE per invocation (N+1 discipline,
mirrors the MCP b5efc0b fix); the duplicate-summary guard refuses (exit 3)
when an OmniForge summary note newer than --since exists, is never evaluated
for reply-only invocations, and is overridden by --force; --reply-to /
per-entry reply_to_thread_id route replies onto recorded threads and exclude
those entries from new-thread posting; --dry-run prints the exact commands
and executes nothing; the script never adds text (no AI attribution) to a
body.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

POSTER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
    "skills", "omnireview-gitlab", "scripts", "omni_post_review.py"))

PROJECT = "73279395"
MR = "136"

DIFF_REFS = {"iid": 136, "diff_refs": {
    "base_sha": "aaa111", "head_sha": "bbb222", "start_sha": "ccc333"}}

# created_at of the seeded OmniForge summary note in NOTES_WITH_SUMMARY.
SUMMARY_EPOCH = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc).timestamp()

NOTES_WITH_SUMMARY = json.dumps([
    {"body": "Human comment", "created_at": "2026-08-01T09:00:00Z"},
    {"body": "## OmniForge\n\n**Verdict:** APPROVE_WITH_FIXES",
     "created_at": "2026-09-02T10:00:00Z"},
    {"body": "**Important** — inline note (not a summary)",
     "created_at": "2026-09-02T10:01:00Z"},
])

FINDINGS = [
    {"file_path": "src/app.py", "line_number": 42,
     "body": "**Important** — Missing null check\n\n**What:** cfg.get('x') "
             "dereferenced without a None guard.\n\nConfidence: 82/100 | "
             "Found by: Codebase Reviewer (OmniForge)"},
    {"file_path": "src/util.py", "line_number": 7,
     "body": "**Minor** — Magic number\n\n**What:** unexplained constant 7."
             "\n\nConfidence: 71/100 | Found by: Codebase Reviewer (OmniForge)"},
]

# The shim: logs every invocation ($*), serves fixture JSON on GET
# (GLAB_MR_JSON for the MR view, GLAB_NOTES for the notes list), and scripts
# failure sequences on POST — GLAB_FAIL_NOTES="400" makes the first notes
# POST exit 1 with "HTTP 400" on stderr; a comma list is the per-attempt
# sequence (empty field = success); the counter is per endpoint bucket
# (notes vs discussions*).
GLAB_SHIM = """#!/bin/sh
printf '%s\\n' "$*" >> "$GLAB_LOG"
path=""
method=""
prev=""
for a in "$@"; do
    if [ "$prev" = "--method" ]; then method="$a"; fi
    prev="$a"
    case "$a" in
        projects/*) path="$a" ;;
    esac
done
if [ "$method" = "POST" ]; then
    bucket=notes
    case "$path" in
        */discussions*) bucket=discussions ;;
    esac
    cnt=0
    if [ -f "$GLAB_COUNT_DIR/$bucket" ]; then cnt=$(cat "$GLAB_COUNT_DIR/$bucket"); fi
    cnt=$((cnt + 1))
    echo "$cnt" > "$GLAB_COUNT_DIR/$bucket"
    spec=""
    case "$bucket" in
        discussions) spec="$GLAB_FAIL_DISCUSSIONS" ;;
        notes) spec="$GLAB_FAIL_NOTES" ;;
    esac
    if [ -n "$spec" ]; then
        code=$(printf '%s' "$spec" | awk -F, -v f="$cnt" '{ if (f <= NF) print $f }')
        if [ -n "$code" ]; then
            printf 'HTTP %s: server error body\\n' "$code" >&2
            exit 1
        fi
    fi
    printf '{}\\n'
    exit 0
fi
case "$path" in
    *notes?*) cat "$GLAB_NOTES" ;;
    *) cat "$GLAB_MR_JSON" ;;
esac
exit 0
"""


def write_file(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def load_poster_module():
    """In-process loader for pure-function checks (run_glab backoff)."""
    spec = importlib.util.spec_from_file_location("omni_post_review_under_test",
                                                  POSTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class OmniPostReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = os.path.join(self.tmp, "glab.log")
        self.count_dir = os.path.join(self.tmp, "counts")
        os.makedirs(self.count_dir)
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        shim = write_file(os.path.join(bin_dir, "glab"), GLAB_SHIM)
        os.chmod(shim, 0o755)
        self.mr_json = write_file(os.path.join(self.tmp, "mr.json"),
                                  json.dumps(DIFF_REFS))
        self.notes_json = write_file(os.path.join(self.tmp, "notes.json"), "[]")
        self.env = dict(os.environ)
        self.env["PATH"] = bin_dir + os.pathsep + self.env.get("PATH", "")
        self.env.update(GLAB_LOG=self.log, GLAB_COUNT_DIR=self.count_dir,
                        GLAB_MR_JSON=self.mr_json, GLAB_NOTES=self.notes_json)

    def set_notes(self, notes_json_text):
        write_file(self.notes_json, notes_json_text)

    def run_poster(self, findings, extra=(),
                   summary="## OmniForge\n\n**Verdict:** APPROVE\n"):
        spath = write_file(os.path.join(self.tmp, "summary.md"), summary)
        fpath = write_file(os.path.join(self.tmp, "findings.json"),
                           json.dumps(findings))
        argv = [sys.executable, POSTER,
                "--mr", MR, "--project", PROJECT,
                "--summary", spath, "--findings-json", fpath,
                "--backoff-base", "0"] + list(extra)
        return subprocess.run(argv, env=self.env, capture_output=True,
                              text=True)

    def raw_poster(self, *argv):
        return subprocess.run([sys.executable, POSTER] + list(argv),
                              env=self.env, capture_output=True, text=True)

    def log_text(self):
        if not os.path.exists(self.log):
            return ""
        with open(self.log, encoding="utf-8") as fh:
            return fh.read()

    def bucket(self, name):
        p = os.path.join(self.count_dir, name)
        if not os.path.exists(p):
            return 0
        with open(p, encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)

    def stdout_json(self, result):
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        return json.loads(lines[-1])

    # ── retry / fail-fast ─────────────────────────────────────

    def test_retry_on_5xx_then_success(self):
        self.env["GLAB_FAIL_DISCUSSIONS"] = "500"
        r = self.run_poster(FINDINGS)
        self.assertEqual(r.returncode, 0, r.stderr)
        # thread 1: attempt fails then succeeds (2 calls); thread 2: 1 call
        self.assertEqual(self.bucket("discussions"), 3)
        self.assertEqual(self.bucket("notes"), 1)
        out = self.stdout_json(r)
        self.assertTrue(out["posted_summary"])
        self.assertEqual(out["threads"], 2)
        self.assertEqual(out["replies"], 0)
        self.assertEqual(out["failures"], 0)
        self.assertFalse(out["dry_run"])

    def test_retry_on_429_then_success(self):
        self.env["GLAB_FAIL_DISCUSSIONS"] = "429"
        r = self.run_poster(FINDINGS)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.bucket("discussions"), 3)
        self.assertEqual(self.stdout_json(r)["threads"], 2)

    def test_no_retry_on_400_fails_fast(self):
        self.env["GLAB_FAIL_NOTES"] = "400"
        r = self.run_poster(FINDINGS)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.bucket("notes"), 1)        # exactly one attempt
        self.assertEqual(self.bucket("discussions"), 0)  # nothing else posted
        self.assertIn("400", r.stderr)
        self.assertIn("glab api", r.stderr)              # error names the command
        self.assertIn("notes", r.stderr)
        self.assertEqual(self.stdout_json(r)["failures"], 1)

    def test_backoff_schedule_2s_4s(self):
        mod = load_poster_module()
        sleeps = []
        scripted = [(1, "", "HTTP 500: boom"), (1, "", "HTTP 500: boom"),
                    (0, "{}", "")]
        state = {"n": 0}

        def fake_exec(argv):
            result = scripted[state["n"]]
            state["n"] += 1
            return result

        mod._exec = fake_exec
        mod.sleep_fn = sleeps.append
        out = mod.run_glab(["glab", "api", "anything"], 3, 2.0)
        self.assertEqual(out, "{}")
        self.assertEqual(sleeps, [2.0, 4.0])   # base * 2^(attempt-1)

    # ── findings shape + nested-position workaround ───────────

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
        self.assertEqual(self.bucket("discussions"), 3)  # one POST per entry
        log = self.log_text()
        for f in findings:
            self.assertIn(f["body"], log)
        self.assertEqual(self.stdout_json(r)["threads"], 3)

    def test_nested_position_workaround_fields_present(self):
        r = self.run_poster(FINDINGS[:1], extra=["--dry-run"])
        self.assertEqual(r.returncode, 0, r.stderr)
        # One logical command per DRY-RUN: chunk (bodies contain newlines, so
        # a command can span physical lines — chunk on the prefix, not \n).
        chunks = r.stdout.split("DRY-RUN: ")
        inline = [c for c in chunks[1:] if "/discussions --method" in c]
        self.assertEqual(len(inline), 1)
        for token in ("--raw-field", "position[position_type]=text",
                      "position[base_sha]", "position[head_sha]",
                      "position[start_sha]", "position[new_path]=src/app.py",
                      "position[new_line]=42"):
            self.assertIn(token, inline[0])

    # ── dry-run executes nothing ──────────────────────────────

    def test_dry_run_executes_nothing(self):
        r = self.run_poster(FINDINGS, extra=["--dry-run"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log_text(), "")            # shim never invoked
        self.assertIn("DRY-RUN:", r.stdout)
        out = self.stdout_json(r)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["threads"], 2)

    # ── reply routing ─────────────────────────────────────────

    def test_reply_to_posts_on_recorded_thread(self):
        r = self.run_poster(FINDINGS[:1], extra=["--dry-run", "--reply-to", "T1"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("discussions/T1/notes", r.stdout)
        # no new inline thread, no summary note
        self.assertNotIn("/merge_requests/%s/discussions --method" % MR, r.stdout)
        self.assertNotIn("/merge_requests/%s/notes --method" % MR, r.stdout)
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
        log = self.log_text()
        self.assertEqual(len(re.findall(r"/discussions --method POST", log)), 1)
        self.assertIn("discussions/T7/notes --method POST", log)
        out = self.stdout_json(r)
        self.assertEqual(out["threads"], 1)              # reply entry excluded
        self.assertEqual(out["replies"], 1)
        self.assertTrue(out["posted_summary"])           # mixed batch: one summary

    # ── duplicate-summary guard ───────────────────────────────

    def test_duplicate_summary_guard(self):
        self.set_notes(NOTES_WITH_SUMMARY)
        r = self.run_poster(FINDINGS, extra=["--since", str(SUMMARY_EPOCH - 100)])
        self.assertEqual(r.returncode, 3)
        self.assertNotIn("--method POST", self.log_text())  # refused, posted nothing
        self.assertIn("OmniForge", r.stderr)
        forced = self.run_poster(
            FINDINGS, extra=["--since", str(SUMMARY_EPOCH - 100), "--force"])
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertEqual(self.bucket("notes"), 1)

    def test_summary_guard_ignores_same_run_summary(self):
        self.set_notes(NOTES_WITH_SUMMARY)
        r = self.run_poster(FINDINGS[:1], extra=["--reply-to", "T1"])
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.log_text()
        self.assertNotIn("/merge_requests/%s/notes --method" % MR, log)
        self.assertNotIn("notes?per_page=100", log)      # guard never evaluated
        self.assertIn("discussions/T1/notes --method POST", log)
        out = self.stdout_json(r)
        self.assertEqual(out["replies"], 1)
        self.assertFalse(out["posted_summary"])

    def test_guard_passes_when_note_older_than_since(self):
        self.set_notes(NOTES_WITH_SUMMARY)
        r = self.run_poster(FINDINGS, extra=["--since", str(SUMMARY_EPOCH + 100)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.bucket("notes"), 1)        # summary posted

    # ── single summary note + N+1 refs discipline ─────────────

    def test_posts_exactly_one_summary_note(self):
        r = self.run_poster(FINDINGS)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.bucket("notes"), 1)

    def test_diff_refs_fetched_once(self):
        findings = [{"file_path": "src/f%d.py" % i, "line_number": i + 1,
                     "body": "**Minor** — finding %d" % i} for i in range(5)]
        r = self.run_poster(findings)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.bucket("discussions"), 5)
        refs_calls = re.findall(r"merge_requests/%s(?= |$)" % MR,
                                self.log_text(), re.M)
        self.assertEqual(len(refs_calls), 1)  # exactly one refs GET for N findings

    # ── bodies stay caller-authored ───────────────────────────

    def test_no_ai_attribution_in_bodies(self):
        r = self.run_poster(FINDINGS)
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.log_text()
        self.assertNotIn("Generated by", log)
        self.assertNotIn("Claude", log)

    # ── usage errors ──────────────────────────────────────────

    def test_usage_error_exit_2(self):
        r = self.raw_poster("--mr", MR, "--project", PROJECT)  # missing required
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.strip(), "")     # usage prints no stdout JSON
        spath = write_file(os.path.join(self.tmp, "summary.md"),
                           "## OmniForge\n")
        bad = write_file(os.path.join(self.tmp, "bad.json"), "{not json")
        r2 = self.raw_poster("--mr", MR, "--project", PROJECT,
                             "--summary", spath, "--findings-json", bad)
        self.assertEqual(r2.returncode, 2)
        self.assertIn("findings", r2.stderr)
        self.assertEqual(r2.stdout.strip(), "")

    # ── dry-run mirrors real-run guard behavior ───────────────

    def test_dry_run_force_skips_guard_command(self):
        # A real --force run never lists notes; dry-run must match.
        r = self.run_poster(FINDINGS, extra=["--dry-run", "--force"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("notes?per_page=100", r.stdout)
        r2 = self.run_poster(FINDINGS, extra=["--dry-run"])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("notes?per_page=100", r2.stdout)


if __name__ == "__main__":
    unittest.main()
