"""E3 seam tests — the plugin sweep script's engine-ledger consumption.

The engine's seam dispatcher (engine repo, WP-E3) invokes this script with
`--ledger <engine mr-state path>`. The ledger is READ-ONLY here (the engine
writes it back — mr_state.py's single-writer rule): this file pins exactly
what the plugin consumes and what extra fields its JSON result carries so
the dispatcher can publish (create-vs-edit, transitions) and report
(residual decision, model degradation, timings). Fixtures are e3_-prefixed.
"""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                       "skills", "omnicheck-gitlab", "scripts"))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(SCRIPTS, filename + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


omni_sweep = _load("omni_sweep_e3", "omni_sweep")
omni_verdict = _load("omni_verdict_e3", "omni_verdict")


def e3_repo(root, name="e3_repo"):
    path = str(root / name)
    os.makedirs(path)
    def git(*args):
        return subprocess.run(["git", "-C", path, *args], check=True,
                              capture_output=True, text=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    return path, git


def e3_write(path, rel, content):
    full = os.path.join(path, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)


def e3_commit(git, msg):
    git("add", "-A")
    git("commit", "-q", "-m", msg)


def e3_sha(path):
    return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def e3_finding(fid, body, file_path, line, severity="important", kind="code"):
    return {"id": fid, "disposition": "not_fixed", "severity": severity,
            "kind": kind, "file_path": file_path, "line_number": line,
            "body": body, "reason": ""}


class E3Fixture:
    """A tiny two-commit repo (a real fix hunk for f1 + a docs residual) and
    the findings/report paths one sweep needs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="e3_seam_")
        self.root = pathlib.Path(self._tmp.name)
        self.path, self.git = e3_repo(self.root)
        e3_write(self.path, "src/app.py",
                 "def run():\n    return 1\n\ndef other():\n    return 2\n")
        e3_commit(self.git, "base")
        self.reviewed = e3_sha(self.path)
        # the fix lands CROSS-FILE (src/app.py's locus untouched — a delta
        # on the locus line itself reanchors to obsolete, P2 semantics)
        e3_write(self.path, "src/runners.py",
                 "def run():\n    return guarded()\n\ndef guarded():\n    return 1\n")
        e3_write(self.path, "docs/notes.md", "# Notes\nnew\n")
        e3_commit(self.git, "fix + docs")
        self.head = e3_sha(self.path)
        self.findings_file = self.root / "e3_findings.json"
        self.findings_file.write_text(json.dumps(
            [e3_finding("f1", "run() is unguarded", "src/app.py", 2)]))
        self.report = self.root / "e3_report.md"
        self.breadcrumb = self.root / "e3_breadcrumb.md"

    def tearDown(self):
        self._tmp.cleanup()

    def ledger_file(self, **over):
        state = {"version": 1, "project_id": 7, "mr_iid": 9,
                 "reviewed_head": self.reviewed, "reviewed_at": 1.0,
                 "sweep_head": None, "report_note_id": None,
                 "last_verdicts": [], "residual": None,
                 "counters": {"date": "2026-09-11", "sweeps": 0,
                              "delta_reviews": 0, "re_arms": 0,
                              "push_checks": 1},
                 "head_pending": False, "opt_out": False}
        state.update(over)
        path = self.root / "e3_ledger.json"
        path.write_text(json.dumps(state))
        return str(path)

    def run_main(self, *extra):
        argv = ["--repo-root", self.path, "--reviewed-head", self.reviewed,
                "--head", self.head, "--findings", str(self.findings_file),
                "--model", "none",
                "--out-report", str(self.report),
                "--out-breadcrumb", str(self.breadcrumb), *extra]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = omni_sweep.main(argv)
        self.assertEqual(rc, 0)
        return json.loads(out.getvalue())


class TestE3LedgerConsumption(E3Fixture, unittest.TestCase):

    def test_e3_opt_out_ledger_skips(self):
        result = self.run_main("--ledger",
                               self.ledger_file(opt_out=True))
        self.assertEqual(result["skip"], "SKIP_OPT_OUT")
        self.assertEqual(result["verdicts"], [])
        self.assertFalse(result["report"])

    def test_e3_head_equals_sweep_head_skips(self):
        result = self.run_main("--ledger",
                               self.ledger_file(sweep_head=self.head))
        self.assertEqual(result["skip"], "SKIP_ALREADY_SWEPT")

    def test_e3_sweep_number_defaults_to_counters_plus_one(self):
        result = self.run_main("--ledger", self.ledger_file())
        self.assertEqual(result["sweep_number"], 1)
        st = json.loads(open(self.ledger_file(
            counters={"date": "2026-09-11", "sweeps": 3, "delta_reviews": 0,
                      "re_arms": 0, "push_checks": 5})).read())
        path = str(self.root / "e3_ledger.json")
        result = self.run_main("--ledger", path)
        self.assertEqual(result["sweep_number"], 4)
        self.assertIn("(sweep #4)", result["breadcrumb"])

    def test_e3_explicit_sweep_number_wins_over_ledger(self):
        result = self.run_main("--ledger", self.ledger_file(),
                               "--sweep-number", "9")
        self.assertEqual(result["sweep_number"], 9)

    def test_e3_transitions_vs_last_verdicts(self):
        ledger = self.ledger_file(last_verdicts=[
            {"thread_id": "f1", "verdict": "not_fixed",
             "checked_head": self.reviewed, "evidence_hunk": None,
             "at": 1.0},
            {"thread_id": "f9", "verdict": "fixed",
             "checked_head": self.reviewed, "evidence_hunk": None,
             "at": 1.0}])
        result = self.run_main("--ledger", ledger)
        # model none => every open finding needs_judgment; f1's memo said
        # not_fixed so needs_judgment IS a transition; f9 has no current
        # verdict (not in this sweep's findings) => no transition row
        self.assertEqual(result["transitions"],
                         [{"thread_id": "f1", "from": "not_fixed",
                           "to": "needs_judgment"}])

    def test_e3_identical_repeats_suppressed(self):
        ledger = self.ledger_file(last_verdicts=[
            {"thread_id": "f1", "verdict": "needs_judgment",
             "checked_head": self.reviewed, "evidence_hunk": None,
             "at": 1.0}])
        result = self.run_main("--ledger", ledger)
        self.assertEqual(result["transitions"], [])

    def test_e3_publish_hint_create_then_edit(self):
        result = self.run_main("--ledger", self.ledger_file())
        self.assertEqual(result["publish"],
                         {"action": "create", "note_id": None})
        result = self.run_main("--ledger", self.ledger_file(
            report_note_id=4242))
        self.assertEqual(result["publish"],
                         {"action": "edit", "note_id": 4242})

    def test_e3_unreadable_ledger_degrades_not_skips(self):
        bad = self.root / "e3_ledger_bad.json"
        bad.write_text("{not json")
        result = self.run_main("--ledger", str(bad))
        self.assertIsNone(result["skip"])
        self.assertTrue(result["ledger_error"])
        self.assertEqual(result["sweep_number"], 1)
        self.assertEqual(result["publish"]["action"], "create")

    def test_e3_absent_ledger_stays_the_p2_contract(self):
        result = self.run_main()
        self.assertIsNone(result["skip"])
        self.assertNotIn("ledger_error", result)
        self.assertTrue(result["report"])


class TestE3ConcernCarries(E3Fixture, unittest.TestCase):
    """The seam's concern-carrying contract (found live on the dogfood MR:
    the model answered 'the concern text is empty'): a {threads: [...]}
    payload classified through omni_verdict must carry the thread BODY into
    the finding record — it is the evidence packet's concern text."""

    def test_e3_classified_thread_carries_body(self):
        threads = [{"id": "t1", "resolved": False,
                    "file_path": "src/app.py", "line_number": 2,
                    "body": "**Important**: `run()` returns unguarded",
                    "replies": []}]
        findings, _ = omni_verdict.classify_run(threads)
        self.assertEqual(findings[0].get("body"),
                         "**Important**: `run()` returns unguarded")

    def test_e3_sweep_packet_has_concern_text(self):
        class RecordingProvider:
            prompt = None

            def call(self, prompt, session_id=None):
                type(self).prompt = prompt
                return json.dumps([{
                    "finding_id": "t1", "verdict": "not_fixed",
                    "evidence_hunk_id": None, "confidence": 60,
                    "one_line": "no"}])

        omni_sweep.run_sweep(
            repo_root=self.path, reviewed_head=self.reviewed, head=self.head,
            findings=[{"id": "t1", "disposition": "not_fixed",
                       "severity": "important", "kind": "code",
                       "file_path": "src/app.py", "line_number": 2,
                       "body": "`run()` must guard its return",
                       "reason": ""}],
            provider=RecordingProvider(), sweep_number=1)
        self.assertIn("`run()` must guard its return",
                      RecordingProvider.prompt)


class TestE3SelfIngestion(unittest.TestCase):
    """The sweep's own posted output must never come back as a finding
    (found live on dogfood MR !29: the living report and the breadcrumb
    notes returned as fetched threads and were classified as findings on
    the NEXT sweep). is_artifact owns the artifact vocabulary."""

    def test_e3_living_report_body_is_artifact(self):
        body = ("**Push sweep report** — `42892070..a5e0ad44`\n\n"
                "| finding | severity | kind | verdict | evidence | note |")
        self.assertTrue(omni_verdict.is_artifact(body))

    def test_e3_breadcrumb_body_is_artifact(self):
        body = ("**Push check complete — head `a5e0ad44`** (sweep #2)\n\n"
                "- Fixed since review: 0 findings")
        self.assertTrue(omni_verdict.is_artifact(body))

    def test_e3_transition_reply_body_is_artifact(self):
        body = "Push sweep at `1b5c9fba`: verdict not_fixed (was needs_judgment)."
        self.assertTrue(omni_verdict.is_artifact(body))

    def test_e3_model_degraded_banner_is_artifact(self):
        body = ("**⚠ Model leg unavailable this sweep — open findings "
                "deferred to judgment, nothing auto-closed.**\n\n"
                "**Push sweep report**")
        self.assertTrue(omni_verdict.is_artifact(body))

    def test_e3_human_finding_is_not_swallowed(self):
        # the guard must not over-block: a finding that QUOTES the report
        # header mid-body (not at the start) stays a finding
        body = ("The sweep said \"**Push sweep report**\" but my concern "
                "**Important** here is real")
        self.assertFalse(omni_verdict.is_artifact(body))


class TestE3ResidualDecision(E3Fixture, unittest.TestCase):

    def test_e3_code_residual_trips_threshold(self):
        met, reason = omni_sweep.residual_threshold(
            {"files": ["src/new.py"], "additions": 5, "deletions": 0,
             "shapes": ["code", "new file"]})
        self.assertTrue(met)
        self.assertIn("non-test", reason)

    def test_e3_docs_only_residual_does_not_trip(self):
        met, reason = omni_sweep.residual_threshold(
            {"files": ["docs/a.md"], "additions": 5, "deletions": 0,
             "shapes": ["docs"]})
        self.assertFalse(met)

    def test_e3_manifest_shape_trips(self):
        met, _ = omni_sweep.residual_threshold(
            {"files": ["pyproject.toml"], "additions": 1, "deletions": 0,
             "shapes": ["manifest"]})
        self.assertTrue(met)

    def test_e3_size_threshold_trips(self):
        met, _ = omni_sweep.residual_threshold(
            {"files": ["a.md", "b.md", "c.md", "d.md"],
             "additions": 4, "deletions": 0, "shapes": ["docs"]})
        self.assertTrue(met)

    def test_e3_result_carries_residual_decision(self):
        result = self.run_main("--ledger", self.ledger_file())
        self.assertIn("residual_decision", result)
        self.assertIn("threshold_met", result["residual_decision"])
        # docs/notes.md is the only residual here: docs-only, tiny
        self.assertFalse(result["residual_decision"]["threshold_met"])


class TestE3ModelDegradation(E3Fixture, unittest.TestCase):

    def test_e3_model_failure_degrades_to_needs_judgment(self):
        class DeadProvider:
            def call(self, prompt, session_id=None):
                raise RuntimeError("claude -p failed: boom")

        result = omni_sweep.run_sweep(
            repo_root=self.path, reviewed_head=self.reviewed, head=self.head,
            findings=[e3_finding("f1", "run() is unguarded", "src/app.py", 2)],
            provider=DeadProvider(), sweep_number=1)
        self.assertIsNone(result["skip"])
        self.assertTrue(result["model_degraded"])
        self.assertEqual(result["verdicts"][0]["verdict"], "needs_judgment")
        self.assertIn("model unavailable", result["verdicts"][0]["reason"])
        self.assertTrue(result["report"])

    def test_e3_model_reply_without_json_degrades_too(self):
        class GarbageProvider:
            def call(self, prompt, session_id=None):
                return "I am not a JSON array at all"

        result = omni_sweep.run_sweep(
            repo_root=self.path, reviewed_head=self.reviewed, head=self.head,
            findings=[e3_finding("f1", "run() is unguarded", "src/app.py", 2)],
            provider=GarbageProvider(), sweep_number=1)
        self.assertTrue(result["model_degraded"])
        self.assertEqual(result["verdicts"][0]["verdict"], "needs_judgment")


class TestE3Timings(E3Fixture, unittest.TestCase):

    def test_e3_result_carries_per_leg_timings(self):
        result = self.run_main("--ledger", self.ledger_file())
        timings = result["timings"]
        self.assertIn("code_s", timings)
        self.assertIn("model_s", timings)
        self.assertGreaterEqual(timings["code_s"], 0.0)
        self.assertGreaterEqual(timings["model_s"], 0.0)


class TestE3SessionsContract(E3Fixture, unittest.TestCase):
    """D11: the stdout result contract carries the session ids used (call
    order) — the dispatcher's TRANSCRIPT emission (SC-6) reads it. Skip /
    --model none paths report the empty list; no key is ever removed."""

    def test_e3_model_none_result_carries_empty_sessions(self):
        result = self.run_main("--ledger", self.ledger_file())
        self.assertIn("sessions", result)
        self.assertEqual(result["sessions"], [])

    def test_e3_skip_results_carry_empty_sessions(self):
        result = self.run_main("--ledger", self.ledger_file(opt_out=True))
        self.assertEqual(result.get("sessions"), [])
        result = self.run_main("--ledger",
                               self.ledger_file(sweep_head=self.head))
        self.assertEqual(result.get("sessions"), [])


FAKE_CLAUDE = (
    "#!/usr/bin/env python3\n"
    "import json, os, re, sys\n"
    "args = sys.argv[1:]\n"
    "prompt = args[args.index('-p') + 1]\n"
    "sid = args[args.index('--session-id') + 1] "
    "if '--session-id' in args else None\n"
    "ids = re.findall(r'\"finding_id\": \"(f\\d+)\"', prompt)\n"
    "with open(os.environ['SWEEPFIX_FAKE_LOG'], 'a') as fh:\n"
    "    fh.write(json.dumps({'session_id': sid, 'ids': ids}) + '\\n')\n"
    "print(json.dumps([{'finding_id': i, 'verdict': 'not_fixed',\n"
    "                   'evidence_hunk_id': None, 'confidence': 80,\n"
    "                   'one_line': 'e2e'} for i in ids]))\n")


class TestE3ChunkOverrideEndToEnd(E3Fixture, unittest.TestCase):
    """The env chunk knob works through a REAL script invocation: a fake
    `claude` on PATH records every spawn (argv session id + the finding
    ids in its prompt); OMNIFORGE_SWEEP_CHUNK_THRESHOLD=4 against 5
    findings yields two spawns and a two-entry sessions list."""

    def test_e3_env_chunk_override_two_spawns_and_sessions(self):
        bindir = self.root / "bin"
        bindir.mkdir()
        fake = bindir / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        log = self.root / "fake_claude_log.jsonl"
        self.findings_file.write_text(json.dumps([
            e3_finding("f%d" % i, "run() is unguarded", "src/app.py", 2)
            for i in range(1, 6)]))
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("OMNIFORGE_", "SWEEPFIX_"))
               and k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        env["OMNIFORGE_SWEEP_CHUNK_THRESHOLD"] = "4"
        env["OMNIFORGE_SESSION_ID"] = "e2e-env-sid"
        env["SWEEPFIX_FAKE_LOG"] = str(log)
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "omni_sweep.py"),
             "--repo-root", self.path, "--reviewed-head", self.reviewed,
             "--head", self.head, "--findings", str(self.findings_file),
             "--model", "claude-spawn"],
            capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(len(calls), 2)                # threshold 4 < 5
        self.assertEqual(calls[0]["ids"], ["f1", "f2", "f3"])  # ceil(5/2)
        self.assertEqual(calls[1]["ids"], ["f4", "f5"])
        self.assertEqual(calls[0]["session_id"], "e2e-env-sid")
        self.assertNotEqual(calls[1]["session_id"], "e2e-env-sid")
        self.assertIsNotNone(calls[1]["session_id"])
        self.assertEqual(result["sessions"][0], "e2e-env-sid")
        self.assertEqual(result["sessions"][1], calls[1]["session_id"])
        self.assertEqual([v["finding_id"] for v in result["verdicts"]],
                         ["f1", "f2", "f3", "f4", "f5"])


if __name__ == "__main__":
    unittest.main()
