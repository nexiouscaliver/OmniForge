"""Contract tests for the machine-readable findings validator
(omni_validate_findings.py).

The validator is invoked as a SUBPROCESS (exit codes + the one-JSON-line
stdout contract are exercised in a real process; same convention as
test_omni_wait.py); pure locator checks load the module in-process via
importlib. Stdlib only — no pytest import — so the module runs identically
under bare unittest and pytest.

Pinned semantics (spec D2): `[]` is a VALID zero-findings report (zero is
signal, NOT pass-through); no block / unparseable block / zero valid findings
is pass-through — exit 0, findings [], prose report never modified; a failing
finding is dropped to anomalies (never clamped, never coerced); usage errors
are the only exit 2.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

VALIDATOR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
    "skills", "omnireview-gitlab", "scripts", "omni_validate_findings.py"))

PROSE = "# Codebase Reviewer (OmniForge)\n\n**Finding 1**\n- prose finding body\n"


def load_validator_module():
    """In-process loader for pure-function checks (find_block locator)."""
    spec = importlib.util.spec_from_file_location("omni_validate_findings_under_test",
                                                  VALIDATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tmp_dir(testcase):
    d = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


def finding(**overrides):
    """A minimal well-formed codebase finding; tests override or del fields."""
    f = {
        "agent": "codebase",
        "file": "src/app.py",
        "line_range": [42, 44],
        "category": "logic",
        "severity": "important",
        "confidence": 82,
        "one_liner": "Missing None guard before dict access",
        "evidence": "service.py:88 calls cfg.get('x') and dereferences without a None check",
    }
    f.update(overrides)
    return f


def json_block(entries):
    return "```json\n" + json.dumps(entries, indent=2) + "\n```\n"


def report_text(entries):
    return PROSE + "\n" + json_block(entries)


def write_report(d, name, text):
    path = os.path.join(d, name)
    with open(path, "w") as f:
        f.write(text)
    return path


def run_validator(report, agent, out=None, timeout=30):
    args = [sys.executable, VALIDATOR, "--report", report, "--agent", agent]
    if out is not None:
        args += ["--out", out]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def stdout_json(proc):
    """stdout must be exactly one JSON line; parse and return it."""
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if len(lines) != 1:
        raise ValueError("expected exactly one stdout JSON line, got %d: %r" % (
            len(lines), proc.stdout[:400]))
    return json.loads(lines[0])


def default_out(report):
    return os.path.splitext(report)[0] + ".findings.json"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestOmniValidateFindings(unittest.TestCase):
    def test_final_json_block_array_parses(self):
        d = tmp_dir(self)
        report = write_report(d, "reviewer.md", report_text([finding()]))
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st, {"ok": True, "validated": 1, "anomalies": 0,
                              "passthrough": False})
        data = load_json(default_out(report))       # default findings path
        self.assertEqual(data["agent"], "codebase")
        self.assertFalse(data["passthrough"])
        self.assertEqual(data["anomalies"], [])
        self.assertEqual(len(data["findings"]), 1)
        out = data["findings"][0]
        self.assertEqual(out["agent"], "codebase")  # identity stamped from --agent
        self.assertEqual(out["file"], "src/app.py")
        self.assertEqual(out["line_range"], [42, 44])
        self.assertEqual(out["confidence"], 82)
        # locator unit check: the block finder returns the parsed array
        mod = load_validator_module()
        self.assertEqual(mod.find_block(report_text([finding()])), [finding()])

    def test_empty_array_zero_findings_valid(self):
        d = tmp_dir(self)
        report = write_report(d, "reviewer.md", PROSE + "\n```json\n[]\n```\n")
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st, {"ok": True, "validated": 0, "anomalies": 0,
                              "passthrough": False})
        data = load_json(default_out(report))
        self.assertEqual(data["findings"], [])
        self.assertFalse(data["passthrough"])       # zero is signal — NOT pass-through
        self.assertEqual(data["anomalies"], [])

    def test_missing_block_passthrough_exit0(self):
        d = tmp_dir(self)
        report = write_report(d, "reviewer.md",
                              PROSE + "No machine block anywhere in this report.\n")
        before = open(report, "rb").read()
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st, {"ok": True, "validated": 0, "anomalies": 1,
                              "passthrough": True})
        data = load_json(default_out(report))
        self.assertEqual(data["findings"], [])
        self.assertTrue(data["passthrough"])
        self.assertEqual(len(data["anomalies"]), 1)
        self.assertIn("pass-through", data["anomalies"][0])
        self.assertEqual(open(report, "rb").read(), before)

    def test_garbled_block_passthrough_exit0(self):
        d = tmp_dir(self)
        report = write_report(d, "reviewer.md",
                              PROSE + "\n```json\n[{\"agent\": \"codebase\", \"confidence\": \n```\n")
        before = open(report, "rb").read()
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st, {"ok": True, "validated": 0, "anomalies": 1,
                              "passthrough": True})
        data = load_json(default_out(report))
        self.assertEqual(data["findings"], [])
        self.assertTrue(data["passthrough"])
        self.assertEqual(len(data["anomalies"]), 1)
        self.assertIn("pass-through", data["anomalies"][0])
        self.assertEqual(open(report, "rb").read(), before)

    def test_report_file_untouched_on_passthrough(self):
        d = tmp_dir(self)
        garbled = write_report(d, "garbled.md", PROSE + "\n```json\nnot json\n```\n")
        valid = write_report(d, "valid.md", report_text([finding()]))
        before_garbled = open(garbled, "rb").read()
        before_valid = open(valid, "rb").read()
        for report, agent in ((garbled, "codebase"), (valid, "codebase")):
            proc = run_validator(report, agent, out=os.path.join(d, "o.json"))
            self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(open(garbled, "rb").read(), before_garbled)
        self.assertEqual(open(valid, "rb").read(), before_valid)

    def test_confidence_out_of_range_dropped(self):
        d = tmp_dir(self)
        entries = [finding(), finding(confidence=105)]
        report = write_report(d, "reviewer.md", report_text(entries))
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st, {"ok": True, "validated": 1, "anomalies": 1,
                              "passthrough": False})
        data = load_json(default_out(report))
        self.assertEqual([f["confidence"] for f in data["findings"]], [82])
        self.assertIn("finding 2: confidence 105 out of range — dropped",
                      data["anomalies"])

    def test_line_range_shape_enforced(self):
        d = tmp_dir(self)
        entries = [
            finding(),                        # idx 1: [42, 44] ok
            finding(line_range=[42]),         # idx 2: 1-int array — dropped
            finding(line_range=[1, "x"]),     # idx 3: non-int member — dropped
            finding(line_range=None),         # idx 4: null ok
        ]
        report = write_report(d, "reviewer.md", report_text(entries))
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st, {"ok": True, "validated": 2, "anomalies": 2,
                              "passthrough": False})
        data = load_json(default_out(report))
        self.assertEqual([f["line_range"] for f in data["findings"]],
                         [[42, 44], None])
        self.assertIn("finding 2: line_range [42] is not a 2-int array or null — dropped",
                      data["anomalies"])
        self.assertIn("finding 3: line_range [1, 'x'] is not a 2-int array or null — dropped",
                      data["anomalies"])

    def test_agent_mismatch_anomaly_nonfatal(self):
        d = tmp_dir(self)
        report = write_report(d, "reviewer.md", report_text([finding(agent="security")]))
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st, {"ok": True, "validated": 1, "anomalies": 1,
                              "passthrough": False})
        data = load_json(default_out(report))
        self.assertEqual(len(data["findings"]), 1)
        self.assertEqual(data["findings"][0]["agent"], "codebase")  # stamped anyway
        self.assertEqual(data["anomalies"],
                         ["finding 1: agent 'security' != 'codebase' (stamped anyway)"])

    def test_stdout_single_json_line(self):
        d = tmp_dir(self)
        report = write_report(d, "reviewer.md", report_text([finding()]))
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.count("\n"), 1)  # exactly one line
        obj = json.loads(proc.stdout)
        self.assertEqual(set(obj), {"ok", "validated", "anomalies", "passthrough"})

    # --- brief-mandated cases beyond the nine named tests ---

    def test_earlier_malformed_block_final_valid_wins(self):
        d = tmp_dir(self)
        text = (PROSE + "\n```json\n[{\"agent\": \"codebase\", broken\n```\n"
                + "\nmore prose between blocks\n\n" + json_block([finding(confidence=70)]))
        report = write_report(d, "reviewer.md", text)
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st, {"ok": True, "validated": 1, "anomalies": 0,
                              "passthrough": False})
        data = load_json(default_out(report))
        self.assertEqual([f["confidence"] for f in data["findings"]], [70])

    def test_last_block_from_eof_wins(self):
        d = tmp_dir(self)
        text = (PROSE + "\n" + json_block([finding(confidence=71)])
                + "\ninterim prose\n\n" + json_block([finding(confidence=88)]))
        report = write_report(d, "reviewer.md", text)
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = load_json(default_out(report))
        self.assertEqual([f["confidence"] for f in data["findings"]], [88])

    def test_missing_or_string_confidence_dropped(self):
        d = tmp_dir(self)
        f2 = finding()
        del f2["confidence"]
        entries = [finding(), f2, finding(confidence="95")]
        report = write_report(d, "reviewer.md", report_text(entries))
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st, {"ok": True, "validated": 1, "anomalies": 2,
                              "passthrough": False})
        data = load_json(default_out(report))
        self.assertEqual([f["confidence"] for f in data["findings"]], [82])
        self.assertIn("finding 2: confidence None out of range — dropped",
                      data["anomalies"])
        self.assertIn("finding 3: confidence '95' out of range — dropped",
                      data["anomalies"])

    def test_line_range_string_dropped(self):
        d = tmp_dir(self)
        entries = [finding(), finding(line_range="42")]
        report = write_report(d, "reviewer.md", report_text(entries))
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = load_json(default_out(report))
        self.assertEqual(len(data["findings"]), 1)
        self.assertIn("finding 2: line_range '42' is not a 2-int array or null — dropped",
                      data["anomalies"])

    def test_extra_fields_preserved_verbatim(self):
        d = tmp_dir(self)
        extra = {"impact": "Crash under load", "attack_scenario": "none",
                 "recommendation": "Guard the None case",
                 "custom": {"nested": [1, 2], "kept": True}}
        report = write_report(d, "reviewer.md", report_text([finding(**extra)]))
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = load_json(default_out(report))
        self.assertEqual(data["findings"], [finding(**extra)])  # verbatim, untouched

    def test_all_findings_dropped_passthrough(self):
        d = tmp_dir(self)
        entries = [finding(confidence=150), finding(severity="mega")]
        report = write_report(d, "reviewer.md", report_text(entries))
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st, {"ok": True, "validated": 0, "anomalies": 3,
                              "passthrough": True})
        data = load_json(default_out(report))
        self.assertEqual(data["findings"], [])
        self.assertTrue(data["passthrough"])
        self.assertEqual(data["anomalies"][-1], "zero valid findings — pass-through")

    def test_usage_error_missing_report_exit2(self):
        proc = subprocess.run([sys.executable, VALIDATOR, "--agent", "codebase"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")   # usage errors carry no JSON line

    def test_usage_error_bad_agent_exit2(self):
        d = tmp_dir(self)
        report = write_report(d, "reviewer.md", report_text([finding()]))
        proc = run_validator(report, "not-an-agent")
        self.assertEqual(proc.returncode, 2)

    def test_missing_report_file_exit2(self):
        proc = run_validator(os.path.join(tempfile.gettempdir(),
                                          "omni_no_such_report.md"), "codebase")
        self.assertEqual(proc.returncode, 2)

    # --- review round 1: out-write hardening ---

    def test_unwritable_out_exit2_no_stdout(self):
        d = tmp_dir(self)
        report = write_report(d, "reviewer.md", report_text([finding()]))
        proc = run_validator(report, "codebase",
                             out=os.path.join(d, "no", "such", "dir", "o.json"))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")   # no JSON line on write failure
        self.assertIn("cannot write", proc.stderr)

    def test_non_ascii_fields_round_trip(self):
        d = tmp_dir(self)
        f = finding(one_liner="Nicht geprüfter None-Zugriff — Absturz möglich",
                    evidence="service.py:88 — cfg.get('x') wird ohne None-Prüfung dereferenziert")
        report = write_report(d, "reviewer.md", report_text([f]))
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = load_json(default_out(report))
        self.assertEqual(data["findings"], [f])

    def test_default_out_stem_handles_dotted_dir(self):
        d = tmp_dir(self)
        sub = os.path.join(d, "v1.2.3")             # dotted dir, dotless basename
        os.makedirs(sub)
        report = write_report(sub, "reviewer", PROSE + "\n```json\n[]\n```\n")
        proc = run_validator(report, "codebase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = os.path.join(sub, "reviewer.findings.json")
        self.assertTrue(os.path.exists(out), out)   # lands NEXT TO the report
        self.assertEqual(load_json(out)["agent"], "codebase")


if __name__ == "__main__":
    unittest.main()
