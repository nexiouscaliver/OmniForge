"""Behavior tests for the chunked subagent-completion waiter (omni_wait.py).

The waiter is invoked as a SUBPROCESS (exit codes + SIGALRM are exercised in a
real process; no import-path coupling). Stdlib only — no pytest import — so the
module runs identically under bare unittest and pytest.

Determinism: fake mtimes via os.utime (age_file), tiny clamped flags, and the
stall clock parked at 60 s everywhere except the stall-semantics tests, so a
slow interpreter startup can never flip a running agent to stalled and race the
alarm/deadline.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

OMNI_WAIT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
    "skills", "omnireview-gitlab", "scripts", "omni_wait.py"))

# copilot review round 2: the subprocess-driven waiter tests exercise the SIGALRM
# chunk machinery — on platforms without POSIX timers the waiter (correctly) fails
# fast degraded, so those tests skip rather than fail there.
ALARM_OK = hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")
requires_alarm = unittest.skipUnless(ALARM_OK, "platform lacks SIGALRM/setitimer")


def load_waiter_module():
    """In-process loader for tests that must patch module internals (e.g. the
    platform-alarm guard) — the subprocess path cannot fake a missing SIGALRM."""
    spec = importlib.util.spec_from_file_location("omni_wait_under_test", OMNI_WAIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# Default tiny flags per test. argparse last-occurrence-wins lets individual
# tests override a single value by appending it after TINY.
TINY = ["--stable-window", 1, "--poll-interval", 0.2, "--chunk-timeout", 3,
        "--total-budget", 6, "--stall-after", 60]


def run_waiter(args, timeout=30):
    return subprocess.run([sys.executable, OMNI_WAIT] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=timeout)


def assistant_record(text=None, tool_use=False):
    content = []
    if tool_use:
        content.append({"type": "tool_use", "id": "t1", "name": "Bash", "input": {"cmd": "x"}})
    if text is not None:
        content.append({"type": "text", "text": text})
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def append_records(path, records):
    with open(path, "a") as f:                      # open+close each time: mtime updates, no buffering
        for r in records:
            f.write(json.dumps(r) + "\n")


def age_file(path, seconds_ago):
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


def status_of(proc):
    """stdout must be exactly one JSON line; parse and return it."""
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if len(lines) != 1:
        raise ValueError("expected exactly one stdout JSON line, got %d: %r" % (
            len(lines), proc.stdout[:400]))
    return json.loads(lines[0])


def tmp_dir(testcase):
    d = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


@requires_alarm
class TestImmediateAllTerminal(unittest.TestCase):
    def _three_terminal(self, d):
        paths = [os.path.join(d, "agent-%d.jsonl" % i) for i in range(3)]
        for p in paths:
            append_records(p, [assistant_record("Review complete.\nStatus: DONE")])
        return paths

    def test_all_terminal_with_marker_exits_0_immediately(self):
        d = tmp_dir(self)
        paths = self._three_terminal(d)
        t0 = time.time()
        proc = run_waiter(TINY + ["--transcript", paths[0], "--transcript", paths[1],
                                  "--transcript", paths[2], "--expect", 3])
        wall = time.time() - t0
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLess(wall, 2.0)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "all_terminal")
        self.assertEqual(len(st["transcripts"]), 3)
        for e in st["transcripts"]:
            self.assertEqual(e["terminal_via"], "marker")
            self.assertIn("Status: DONE", e["final_text"])

    def test_status_json_shape(self):
        d = tmp_dir(self)
        paths = self._three_terminal(d)
        proc = run_waiter(TINY + ["--transcript", paths[0], "--transcript", paths[1],
                                  "--transcript", paths[2], "--expect", 3])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = status_of(proc)
        self.assertEqual(set(st), {"expect", "exit_code", "exit_reason", "ts", "since",
                                   "deadline", "elapsed_s", "transcripts"})
        per_entry = {"path", "state", "terminal_via", "mtime_age_s", "harvested_partial",
                     "final_text", "final_text_truncated", "report_file"}
        for e in st["transcripts"]:
            self.assertEqual(set(e), per_entry)
        self.assertEqual(st["exit_code"], proc.returncode)
        # `since` is the EFFECTIVE anchor deadline was computed from — always
        # numeric, never null (copilot review: the old `null`-when-omitted field
        # was self-inconsistent with the always-numeric deadline).
        self.assertIsInstance(st["since"], (int, float))
        self.assertLessEqual(abs(st["since"] - st["ts"]), 60)
        self.assertEqual(st["deadline"], int(st["since"] + 6))  # TINY --total-budget 6


@requires_alarm
class TestMtimeFallback(unittest.TestCase):
    def _unmarked(self, d):
        p = os.path.join(d, "agent-a.jsonl")
        append_records(p, [assistant_record("Findings without the completion marker.")])
        return p

    def test_terminal_via_mtime_when_marker_absent(self):
        d = tmp_dir(self)
        p = self._unmarked(d)
        age_file(p, 5)
        proc = run_waiter(TINY + ["--transcript", p, "--expect", 1])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "all_terminal")
        e = st["transcripts"][0]
        self.assertEqual(e["terminal_via"], "mtime")
        self.assertEqual(e["state"], "terminal")
        self.assertIn("Findings without the completion marker.", e["final_text"])

    def test_running_while_mtime_unstable(self):
        d = tmp_dir(self)
        p = self._unmarked(d)                # fresh mtime — just written
        # stable window parked at 60 s: with the default 1 s window the fresh
        # mtime would stabilize mid-chunk (alarm 2.85 s) and flip running -> mtime
        proc = run_waiter(TINY + ["--stable-window", 60, "--transcript", p, "--expect", 1])
        self.assertEqual(proc.returncode, 3, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "chunk_elapsed")
        self.assertEqual(st["transcripts"][0]["state"], "running")


@requires_alarm
class TestTornLine(unittest.TestCase):
    def test_torn_trailing_line_skipped(self):
        d = tmp_dir(self)
        p = os.path.join(d, "agent-a.jsonl")
        append_records(p, [assistant_record(tool_use=True),
                           assistant_record("All done. Status: DONE")])
        with open(p, "a") as f:
            f.write('{"type":"assistant","message":{"rol')   # torn partial line, no newline
        proc = run_waiter(TINY + ["--transcript", p, "--expect", 1])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = status_of(proc)
        e = st["transcripts"][0]
        self.assertEqual(e["terminal_via"], "marker")
        self.assertEqual(e["state"], "terminal")


@requires_alarm
class TestPredicate(unittest.TestCase):
    def test_mixed_text_and_tool_use_record_is_running(self):
        # A mixed text+tool_use record is the normal shape of a RUNNING agent
        # mid-tool-call: the tool_use block vetoes terminality even when the
        # record also carries text AND its mtime is stable past the window.
        d = tmp_dir(self)
        paths = [os.path.join(d, "agent-%d.jsonl" % i) for i in range(3)]
        for p in paths:
            append_records(p, [assistant_record("Let me run the test suite.", tool_use=True)])
            age_file(p, 30)              # stable past --stable-window 20, under --stall-after 300
        proc = run_waiter(TINY + ["--stable-window", 20, "--stall-after", 300,
                                  "--transcript", paths[0], "--transcript", paths[1],
                                  "--transcript", paths[2], "--expect", 3])
        self.assertEqual(proc.returncode, 3, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "chunk_elapsed")
        for e in st["transcripts"]:
            self.assertEqual(e["state"], "running")
            self.assertIsNone(e["terminal_via"])

        # Scan-back harvest is independent of the veto: the mixed record still
        # CONTRIBUTES final_text (its own text wins over the earlier text-only
        # record — classify says tool_use, the harvest does not skip it).
        q = os.path.join(d, "agent-mixed.jsonl")
        append_records(q, [assistant_record("Earlier prose."),
                           assistant_record("Let me run the test suite.", tool_use=True)])
        age_file(q, 30)
        proc2 = run_waiter(TINY + ["--stable-window", 20, "--stall-after", 300,
                                   "--transcript", q, "--expect", 1])
        self.assertEqual(proc2.returncode, 3, proc2.stderr)
        e2 = status_of(proc2)["transcripts"][0]
        self.assertEqual(e2["state"], "running")
        self.assertEqual(e2["final_text"], "Let me run the test suite.")


@requires_alarm
class TestStaggered(unittest.TestCase):
    def test_exit_0_promptly_after_last_completion(self):
        d = tmp_dir(self)
        t1 = os.path.join(d, "agent-1.jsonl")
        t2 = os.path.join(d, "agent-2.jsonl")
        t3 = os.path.join(d, "agent-3.jsonl")
        append_records(t1, [assistant_record("One. Status: DONE")])
        append_records(t2, [assistant_record("Two. Status: DONE")])
        append_records(t3, [assistant_record(tool_use=True)])   # running, fresh mtime
        proc = subprocess.Popen(
            [sys.executable, OMNI_WAIT] + [str(a) for a in
                TINY + ["--chunk-timeout", 8, "--transcript", t1, "--transcript", t2,
                        "--transcript", t3, "--expect", 3]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(1.2)
            append_records(t3, [assistant_record("Three done. Status: DONE")])
            out, err = proc.communicate(timeout=15)
        except Exception:
            proc.kill()
            proc.wait()
            raise
        self.assertEqual(proc.returncode, 0, err)
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        st = json.loads(lines[0])
        self.assertLess(st["elapsed_s"], 6)   # did not sit out the chunk
        by_path = {e["path"]: e for e in st["transcripts"]}
        self.assertEqual(by_path[t3]["terminal_via"], "marker")
        self.assertEqual(by_path[t3]["state"], "terminal")


@requires_alarm
class TestStall(unittest.TestCase):
    def test_all_stalled_exits_2_stall(self):
        d = tmp_dir(self)
        paths = [os.path.join(d, "agent-1.jsonl"), os.path.join(d, "agent-2.jsonl")]
        for p in paths:
            append_records(p, [assistant_record("Partial findings..."),
                               assistant_record(tool_use=True)])
            age_file(p, 10)
        proc = run_waiter(TINY + ["--stall-after", 2, "--transcript", paths[0],
                                  "--transcript", paths[1], "--expect", 2])
        self.assertEqual(proc.returncode, 2, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "stall")
        self.assertEqual(len(st["transcripts"]), 2)
        for e in st["transcripts"]:
            self.assertEqual(e["state"], "stalled")
            self.assertTrue(e["harvested_partial"])
            # scan-back past the trailing tool_use record — the partial harvest
            self.assertEqual(e["final_text"], "Partial findings...")

    def test_one_stalled_one_running_keeps_waiting_exit_3(self):
        d = tmp_dir(self)
        t1 = os.path.join(d, "agent-1.jsonl")
        t2 = os.path.join(d, "agent-2.jsonl")
        append_records(t1, [assistant_record("Stalled agent findings."),
                            assistant_record(tool_use=True)])
        age_file(t1, 10)
        append_records(t2, [assistant_record(tool_use=True)])   # running, fresh
        proc = run_waiter(TINY + ["--stall-after", 3, "--chunk-timeout", 2,
                                  "--transcript", t1, "--transcript", t2, "--expect", 2])
        self.assertEqual(proc.returncode, 3, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "chunk_elapsed")   # a single dead agent does NOT fail the wait
        by_path = {e["path"]: e for e in st["transcripts"]}
        e1 = by_path[t1]
        self.assertEqual(e1["state"], "stalled")
        self.assertTrue(e1["harvested_partial"])
        self.assertEqual(e1["final_text"], "Stalled agent findings.")
        self.assertEqual(by_path[t2]["state"], "running")

    def test_missing_transcript_old_since_is_stalled(self):
        d = tmp_dir(self)
        gone = os.path.join(d, "agent-gone.jsonl")   # never created
        live = os.path.join(d, "agent-live.jsonl")
        append_records(live, [assistant_record("Live. Status: DONE")])
        since = time.time() - 10
        proc = run_waiter(TINY + ["--stall-after", 2, "--since", since,
                                  "--transcript", gone, "--transcript", live,
                                  "--expect", 2])
        self.assertEqual(proc.returncode, 2, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "stall")
        by_path = {e["path"]: e for e in st["transcripts"]}
        e = by_path[gone]
        self.assertEqual(e["state"], "stalled")      # age from --since when file missing
        self.assertTrue(e["harvested_partial"])
        self.assertIsNone(e["final_text"])
        self.assertEqual(by_path[live]["state"], "terminal")

    def test_missing_transcript_fresh_since_waits_exit_3(self):
        d = tmp_dir(self)
        gone = os.path.join(d, "agent-gone.jsonl")
        live = os.path.join(d, "agent-live.jsonl")
        append_records(live, [assistant_record("Live. Status: DONE")])
        since = time.time()
        proc = run_waiter(TINY + ["--stall-after", 3, "--chunk-timeout", 2,
                                  "--since", since, "--transcript", gone,
                                  "--transcript", live, "--expect", 2])
        self.assertEqual(proc.returncode, 3, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "chunk_elapsed")
        by_path = {e["path"]: e for e in st["transcripts"]}
        self.assertEqual(by_path[gone]["state"], "missing")   # not yet stalled
        self.assertEqual(by_path[live]["state"], "terminal")


@requires_alarm
class TestBudget(unittest.TestCase):
    def test_budget_exhausted_exits_2(self):
        d = tmp_dir(self)
        p = os.path.join(d, "agent-1.jsonl")
        append_records(p, [assistant_record(tool_use=True)])   # running, fresh
        since = time.time() - 10                              # deadline ~2 s out
        proc = run_waiter(TINY + ["--since", since, "--total-budget", 12,
                                  "--chunk-timeout", 10,       # alarm at 9.5 s — AFTER the deadline
                                  "--transcript", p, "--expect", 1])
        self.assertEqual(proc.returncode, 2, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "budget_exhausted")   # not chunk_elapsed, not stall
        self.assertEqual(st["transcripts"][0]["state"], "running")


@requires_alarm
class TestCountMismatch(unittest.TestCase):
    def test_fewer_than_expect_exits_2_loud(self):
        d = tmp_dir(self)
        paths = [os.path.join(d, "agent-%d.jsonl" % i) for i in (1, 2)]
        for p in paths:
            append_records(p, [assistant_record("Done. Status: DONE")])
        t0 = time.time()
        proc = run_waiter(TINY + ["--transcript", paths[0], "--transcript", paths[1],
                                  "--expect", 3])
        wall = time.time() - t0
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertLess(wall, 2.0)                    # explicit paths get no grace
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "count_mismatch")
        self.assertTrue(proc.stderr.strip())
        self.assertIn("3", proc.stderr)
        # the two completed reviewers' harvest is reported, not discarded
        self.assertEqual(len(st["transcripts"]), 2)
        for e in st["transcripts"]:
            self.assertIn("Status: DONE", e["final_text"])

    def test_explicit_over_count_exits_2(self):
        d = tmp_dir(self)
        paths = [os.path.join(d, "agent-%d.jsonl" % i) for i in range(4)]
        for p in paths:
            append_records(p, [assistant_record("Done. Status: DONE")])
        proc = run_waiter(TINY + ["--transcript", paths[0], "--transcript", paths[1],
                                  "--transcript", paths[2], "--transcript", paths[3],
                                  "--expect", 3])
        self.assertEqual(proc.returncode, 2, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "count_mismatch")   # over-count is loud too
        self.assertEqual(len(st["transcripts"]), 4)             # entries still emitted


@requires_alarm
class TestScanDir(unittest.TestCase):
    def test_scan_dir_resolves_and_excludes_main_session(self):
        d = tmp_dir(self)
        agents = [os.path.join(d, "agent-%d.jsonl" % i) for i in (1, 2, 3)]
        for p in agents:
            append_records(p, [assistant_record("Done. Status: DONE")])
            age_file(p, 100)
        main = os.path.join(d, "9f8c7b6a-1234-5678-9abc-def012345678.jsonl")
        append_records(main, [assistant_record("Main session. Status: DONE")])
        proc = run_waiter(TINY + ["--scan-dir", d, "--expect", 3])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = status_of(proc)
        listed = sorted(e["path"] for e in st["transcripts"])
        self.assertEqual(listed, sorted(agents))

    def test_scan_dir_since_filter(self):
        d = tmp_dir(self)
        since = time.time() - 20
        old = [os.path.join(d, "agent-old-%d.jsonl" % i) for i in (1, 2)]
        fresh = [os.path.join(d, "agent-new-%d.jsonl" % i) for i in (1, 2)]
        for p in old:
            append_records(p, [assistant_record("Done. Status: DONE")])
            age_file(p, 120)               # mtime = since - 100 -> excluded
        for p in fresh:
            append_records(p, [assistant_record("Done. Status: DONE")])
            age_file(p, 10)                # mtime = since + 10 -> kept
        proc = run_waiter(TINY + ["--scan-dir", d, "--expect", 2, "--since", since])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = status_of(proc)
        listed = sorted(e["path"] for e in st["transcripts"])
        self.assertEqual(listed, sorted(fresh))

    def test_scan_dir_count_mismatch_after_grace(self):
        d = tmp_dir(self)
        p = os.path.join(d, "agent-1.jsonl")
        append_records(p, [assistant_record("Done. Status: DONE")])
        since = time.time() - 30           # grace (15 s) long expired
        proc = run_waiter(TINY + ["--scan-dir", d, "--expect", 3, "--since", since,
                                  "--count-grace", 15])
        self.assertEqual(proc.returncode, 2, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "count_mismatch")
        self.assertEqual(len(st["transcripts"]), 1)   # found entry still reported

    def test_scan_dir_count_pending_within_grace(self):
        d = tmp_dir(self)
        p = os.path.join(d, "agent-1.jsonl")
        append_records(p, [assistant_record(tool_use=True)])
        since = time.time() - 2            # inside the 15 s spawn grace
        proc = run_waiter(TINY + ["--scan-dir", d, "--expect", 3, "--since", since,
                                  "--count-grace", 15, "--chunk-timeout", 1])
        self.assertEqual(proc.returncode, 3, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "count_pending")   # waited for, not missing


@requires_alarm
class TestChunkAlarm(unittest.TestCase):
    def test_chunk_elapsed_exits_3(self):
        d = tmp_dir(self)
        paths = [os.path.join(d, "agent-%d.jsonl" % i) for i in range(3)]
        for p in paths:
            append_records(p, [assistant_record(tool_use=True)])   # running, fresh
        t0 = time.time()
        proc = run_waiter(TINY + ["--chunk-timeout", 2, "--total-budget", 60,
                                  "--transcript", paths[0], "--transcript", paths[1],
                                  "--transcript", paths[2], "--expect", 3])
        wall = time.time() - t0
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertLess(wall, 5.0)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "chunk_elapsed")
        for e in st["transcripts"]:
            self.assertEqual(e["state"], "running")


@requires_alarm
class TestUnknownShapes(unittest.TestCase):
    def test_unknown_record_shape_treated_as_running(self):
        # (a) trailing unknown-shape record AFTER the marker: still terminal —
        #     the predicate scans for the last ASSISTANT record
        d = tmp_dir(self)
        p = os.path.join(d, "agent-a.jsonl")
        append_records(p, [assistant_record("Done. Status: DONE")])
        append_records(p, [{"type": "system", "content": "x"}])
        proc = run_waiter(TINY + ["--transcript", p, "--expect", 1])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = status_of(proc)
        e = st["transcripts"][0]
        self.assertEqual(e["terminal_via"], "marker")
        self.assertEqual(e["state"], "terminal")

        # (b) NO assistant record at all: not terminal -> running (bounded by
        #     stall/budget, never falsely terminal)
        q = os.path.join(d, "agent-b.jsonl")
        append_records(q, [{"type": "system", "content": "x"}])
        proc2 = run_waiter(TINY + ["--chunk-timeout", 2, "--transcript", q,
                                   "--expect", 1])
        self.assertEqual(proc2.returncode, 3, proc2.stderr)
        st2 = status_of(proc2)
        self.assertEqual(st2["exit_reason"], "chunk_elapsed")
        self.assertEqual(st2["transcripts"][0]["state"], "running")


@requires_alarm
class TestFinalText(unittest.TestCase):
    def test_final_text_truncation_flag(self):
        d = tmp_dir(self)
        p = os.path.join(d, "agent-big.jsonl")
        text = "x" * (300 * 1024) + "\nStatus: DONE"
        append_records(p, [assistant_record(text)])
        proc = run_waiter(TINY + ["--transcript", p, "--expect", 1])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = status_of(proc)
        e = st["transcripts"][0]
        self.assertTrue(e["final_text_truncated"])
        self.assertLessEqual(len(e["final_text"]), 256 * 1024 + 100)


@requires_alarm
class TestOverhead(unittest.TestCase):
    def test_evaluation_overhead_under_2s(self):
        d = tmp_dir(self)
        paths = [os.path.join(d, "agent-%d.jsonl" % i) for i in range(3)]
        for p in paths:
            records = [assistant_record(tool_use=True) for _ in range(59)]
            records.append(assistant_record("Done. Status: DONE"))
            append_records(p, records)
        t0 = time.time()
        proc = run_waiter(TINY + ["--transcript", paths[0], "--transcript", paths[1],
                                  "--transcript", paths[2], "--expect", 3])
        wall = time.time() - t0
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(status_of(proc)["exit_reason"], "all_terminal")
        self.assertLess(wall, 2.0)


@requires_alarm
class TestReportsDir(unittest.TestCase):
    def test_reports_dir_writes_status_and_per_agent_markdown(self):
        d = tmp_dir(self)
        r = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, r, True)
        a = os.path.join(d, "agent-a.jsonl")
        b = os.path.join(d, "agent-b.jsonl")
        gone = os.path.join(d, "agent-gone.jsonl")     # never created
        append_records(a, [assistant_record("Report A. Status: DONE")])
        append_records(b, [assistant_record("Report B. Status: DONE")])
        with open(os.path.join(r, "omni_wait-stale-old.md"), "w") as f:  # waiter-owned stale
            f.write("stale\n")
        with open(os.path.join(r, "foreign-old.md"), "w") as f:   # NOT waiter-owned
            f.write("foreign\n")
        since = time.time() - 10
        cmd = TINY + ["--stall-after", 2, "--since", since, "--reports-dir", r,
                      "--transcript", a, "--transcript", b, "--transcript", gone,
                      "--expect", 3]
        proc = run_waiter(cmd)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        st = status_of(proc)
        self.assertEqual(st["exit_reason"], "stall")

        with open(os.path.join(r, "status.json")) as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk, st)                  # reports channel == stdout object

        # wipe scope: waiter-owned names only, foreign *.md survives
        self.assertFalse(os.path.exists(os.path.join(r, "omni_wait-stale-old.md")))
        self.assertTrue(os.path.exists(os.path.join(r, "foreign-old.md")))
        md_a = os.path.join(r, "omni_wait-agent-a.jsonl.md")
        md_b = os.path.join(r, "omni_wait-agent-b.jsonl.md")
        md_gone = os.path.join(r, "omni_wait-agent-gone.jsonl.md")
        with open(md_a) as f:
            self.assertEqual(f.read().strip(), "Report A. Status: DONE")
        with open(md_b) as f:
            self.assertEqual(f.read().strip(), "Report B. Status: DONE")
        with open(md_gone) as f:
            self.assertIn("no report harvested", f.read())
        expected_files = sorted(["status.json", "omni_wait-agent-a.jsonl.md",
                                 "omni_wait-agent-b.jsonl.md",
                                 "omni_wait-agent-gone.jsonl.md", "foreign-old.md"])
        self.assertEqual(sorted(os.listdir(r)), expected_files)

        # idempotent re-run: same content, no duplicates
        proc2 = run_waiter(cmd)
        self.assertEqual(proc2.returncode, 2, proc2.stderr)
        st2 = status_of(proc2)
        with open(os.path.join(r, "status.json")) as f:
            self.assertEqual(json.load(f), st2)
        self.assertEqual(sorted(os.listdir(r)), expected_files)
        with open(md_a) as f:
            self.assertEqual(f.read().strip(), "Report A. Status: DONE")
        with open(md_gone) as f:
            self.assertIn("no report harvested", f.read())


    def test_reports_dir_failure_nulls_report_file(self):
        """copilot r4: when the reports-dir write fails and the waiter degrades
        to stdout-only, entries must not advertise report_file paths that were
        never written — consumers would Read nonexistent files."""
        mod = load_waiter_module()
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, "agent-a.jsonl")
        append_records(p, [assistant_record("Done. Status: DONE")])
        blocker = os.path.join(d, "blocker")
        with open(blocker, "w") as f:
            f.write("i am a file, not a directory")
        broken_dir = os.path.join(blocker, "sub")   # makedirs must fail on this
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mod.main(["--transcript", p, "--expect", "1",
                           "--reports-dir", broken_dir])
        self.assertEqual(rc, 0)                     # all-terminal stands; reports are an enhancement
        st = json.loads(out.getvalue().strip().splitlines()[-1])
        for e in st["transcripts"]:
            self.assertIsNone(e["report_file"])
            self.assertEqual(e["final_text"], "Done. Status: DONE")   # harvest still present
        self.assertIn("reports-dir write failed", err.getvalue())

    def test_duplicate_basenames_get_suffixed_reports(self):
        """copilot r3: two transcripts sharing a basename must not overwrite
        each other's report files under --reports-dir."""
        d1 = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d1)
        d2 = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d2)
        a = os.path.join(d1, "agent-dupe.jsonl")
        b = os.path.join(d2, "agent-dupe.jsonl")
        append_records(a, [assistant_record("Report A. Status: DONE")])
        append_records(b, [assistant_record("Report B. Status: DONE")])
        rd = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, rd)
        proc = run_waiter(TINY + ["--transcript", a, "--transcript", b,
                                  "--expect", 2, "--reports-dir", rd])
        st = status_of(proc)
        first = os.path.join(rd, "omni_wait-agent-dupe.jsonl.md")
        second = os.path.join(rd, "omni_wait-agent-dupe.jsonl-2.md")
        with open(first) as f:
            self.assertEqual(f.read().strip(), "Report A. Status: DONE")
        with open(second) as f:
            self.assertEqual(f.read().strip(), "Report B. Status: DONE")
        files = sorted(os.listdir(rd))
        self.assertIn("status.json", files)
        self.assertEqual(files.count("omni_wait-agent-dupe.jsonl.md"), 1)
        self.assertEqual(files.count("omni_wait-agent-dupe.jsonl-2.md"), 1)
        # every entry points at its own distinct report file
        reports = [e["report_file"] for e in st["transcripts"]]
        self.assertEqual(len(set(reports)), 2)
        for r in reports:
            self.assertTrue(os.path.isfile(r), r)


class TestEffectiveSince(unittest.TestCase):
    """copilot review: the JSON `since` field must carry the effective anchor
    (invocation start when --since is omitted) so callers can reconstruct the
    deadline arithmetic; it must never be null."""

    def test_since_defaults_to_invocation_start(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, "agent-a.jsonl")
        append_records(p, [assistant_record("Done. Status: DONE")])
        t0 = time.time()
        proc = run_waiter(TINY + ["--transcript", p, "--expect", 1])
        st = status_of(proc)
        self.assertIsInstance(st["since"], (int, float))
        self.assertGreaterEqual(st["since"], t0 - 1)   # effective start, not epoch 0 / null
        self.assertLessEqual(st["since"], time.time() + 1)

    def test_since_echoes_explicit_value(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, "agent-a.jsonl")
        append_records(p, [assistant_record("Done. Status: DONE")])
        explicit = time.time() - 30
        proc = run_waiter(TINY + ["--transcript", p, "--expect", 1,
                                  "--since", explicit])
        st = status_of(proc)
        self.assertAlmostEqual(st["since"], explicit, delta=0.001)
        self.assertEqual(st["deadline"], int(explicit + 6))  # TINY --total-budget 6


class TestPlatformGuard(unittest.TestCase):
    """copilot review: platforms without SIGALRM/setitimer must fail fast with a
    clear stderr message and a degraded one-line JSON status instead of crashing."""

    def test_sigalrm_unsupported_fails_fast_degraded(self):
        mod = load_waiter_module()
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, "agent-a.jsonl")
        append_records(p, [assistant_record("Done. Status: DONE")])
        mod._platform_supports_alarm = lambda: False
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mod.main(["--transcript", p, "--expect", "1"])
        self.assertEqual(rc, 2)
        st = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(st["exit_code"], 2)
        self.assertEqual(st["exit_reason"], "sigalrm_unsupported")
        self.assertEqual(len(st["transcripts"]), 1)   # best-effort entries still emitted
        self.assertIn("SIGALRM", err.getvalue())

    def test_count_pending_cannot_precede_platform_guard(self):
        """copilot r4: on a platform without POSIX timers, the scan-dir
        spawn-grace branch (exit 3 count_pending) must NOT fire before the
        fail-fast platform guard — 'still running' would be a lie there."""
        mod = load_waiter_module()
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        scan = os.path.join(d, "subagents"); os.makedirs(scan)
        append_records(os.path.join(scan, "agent-1.jsonl"),
                       [assistant_record("Partial. Status: DONE")])
        mod._platform_supports_alarm = lambda: False
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mod.main(["--scan-dir", scan, "--expect", "3",
                           "--since", str(time.time()), "--count-grace", "15"])
        self.assertEqual(rc, 2)
        st = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(st["exit_reason"], "sigalrm_unsupported")
        self.assertIn("SIGALRM", err.getvalue())

    def test_platform_guard_reflects_real_platform(self):
        import signal as _signal
        mod = load_waiter_module()
        self.assertEqual(
            mod._platform_supports_alarm(),
            hasattr(_signal, "SIGALRM") and hasattr(_signal, "setitimer"))


class TestNumericValidation(unittest.TestCase):
    """copilot review round 2: non-positive numeric flags must fail fast on the
    usage path (stderr + exit 2, NO stdout JSON) — a --poll-interval <= 0 would
    make time.sleep raise mid-wait, --stable-window <= 0 would make every
    transcript instantly terminal, --expect < 1 is vacuous."""

    def test_nonpositive_numeric_flags_fail_fast_usage(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, "agent-a.jsonl")
        append_records(p, [assistant_record("Done. Status: DONE")])
        base = ["--transcript", p, "--expect", "1"]
        bad = [["--poll-interval", "0"], ["--poll-interval", "-1"],
               ["--stable-window", "0"], ["--stall-after", "-2"],
               ["--chunk-timeout", "0"], ["--total-budget", "-5"],
               ["--expect", "0"], ["--count-grace", "-1"]]
        for extra in bad:
            proc = run_waiter(base + extra)
            self.assertEqual(proc.returncode, 2, (extra, proc.stdout, proc.stderr))
            self.assertEqual(proc.stdout.strip(), "", extra)   # usage error: no JSON
            self.assertTrue(proc.stderr.strip(), extra)


if __name__ == "__main__":
    unittest.main()
