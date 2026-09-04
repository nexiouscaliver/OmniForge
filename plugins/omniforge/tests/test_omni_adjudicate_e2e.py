"""Mock e2e for the R2-B adjudication chain: turn model, stress fixture,
poster dry-run round-trip, and the generated post-payloads golden.

No LLM is involved — the turn model is the pinned arithmetic
turns(J, B=5) = 1 + max(1, ceil(J / B)) + 1 (1 turn run script + load
worklist, decision batches of >= B judgment rows, 1 turn emit findings JSON +
present report), and the "agent" is compose_post_payloads(), a deterministic
stand-in mirroring the Phase 5 emit rules over the worklist only (auto rows
in order, judgment rows per the frozen default decisions, bodies from the
posting-guide Inline Discussion Thread Template, severity-rank-then-
confidence ordering, replies and notes appended after threads).

The e2e runs the REAL chain in a temp dir over the T1 fixture inputs:
omni_consolidate.py --prior prior.json -> omni_adjudicate.py --clusters
.../clusters.json --findings <three> --out .../adjudication_worklist.json.
The poster round-trip (SC-3) feeds the composed array to the UNTOUCHED
omni_post_review.py --dry-run --skip-summary, which needs no token.

Stdlib only — no pytest import — so the module runs identically under bare
unittest (the README regen command invokes TestE2E.test_payloads_golden
directly with OMNIFORGE_REGEN_GOLDENS=1 to WRITE the golden).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(TESTS_DIR, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "omnireview-gitlab", "scripts")
CONSOLIDATOR = os.path.join(SCRIPTS, "omni_consolidate.py")
ADJUDICATOR = os.path.join(SCRIPTS, "omni_adjudicate.py")
POSTER = os.path.join(SCRIPTS, "omni_post_review.py")
FIXTURES = os.path.join(TESTS_DIR, "fixtures", "adjudication")

GOLDEN_PAYLOADS = os.path.join(FIXTURES, "expected_post_payloads.json")
GOLDEN_WORKLIST = os.path.join(FIXTURES, "expected_adjudication_worklist.json")

THREE_FINDINGS = ["codebase.findings.json", "security.findings.json",
                  "analyst.findings.json"]

SEV_RANK = {"minor": 0, "important": 1, "critical": 2}

# SKILL.md Phase 5: "Only findings with confidence >= 70 enter the final
# report" — the stand-in gates every included-findings path on it.
REPORT_FLOOR = 70


# ── module-level pure helpers (the README regen procedure invokes these) ────


def turns(j, b=5):
    """Pinned turn model: 1 turn run script + load worklist, max(1, ceil(j/b))
    decision batches (batches of at least b judgment rows), 1 turn emit the
    final findings JSON + present the report."""
    return 1 + max(1, -(-j // b)) + 1        # -(-j // b) == ceil(j / b)


def _thread_body(severity, one_liner, why, confidence, agents):
    """The posting-guide Inline Discussion Thread Template filled
    mechanically from verbatim finding fields (why = evidence snippet when
    the row carries one — auto rows carry none)."""
    sev = severity if isinstance(severity, str) else str(severity)
    if not why:
        why = one_liner
    return ("**%s** — %s\n\n**What:** %s\n\n**Why it matters:** %s\n\n"
            "**Recommendation:**\n```\n%s\n```\n\nConfidence: %d/100 | "
            "Found by: %s"
            % (sev.capitalize(), one_liner, one_liner, why, one_liner,
               confidence, agents))


def _enters_report(confidence):
    """The Phase 5 final-report gate — "Only findings with confidence >= 70
    enter the final report" — applied to every included-findings path.
    Prior-thread replies are carry-forward decisions, not inclusions, and
    are never gated here."""
    return isinstance(confidence, int) and confidence >= REPORT_FLOOR


def compose_post_payloads(worklist):
    """Deterministic stand-in agent: mirror the Phase 5 emit rules over the
    worklist only. Auto rows in order (include_once/include -> thread on an
    anchored locus, note when locus.file is null, reply_on_thread -> reply,
    skip_repost -> nothing); judgment rows per the frozen default decisions
    (disagreement present_both / cross_finding keep_both / validity
    keep_each_perspective -> one thread per finding; validity include_or_drop
    sub-threshold -> drop). Only findings with confidence >= 70 enter the
    report. Ordering: severity rank desc, then confidence desc, then stable
    row order; replies and notes appended after threads."""
    decorated, replies, notes = [], [], []

    def thread(severity, confidence, entry):
        decorated.append((SEV_RANK.get(severity, 0), confidence, entry))

    for row in worklist.get("auto_decided", []):
        action = row.get("action")
        if action == "skip_repost":
            continue                          # resolved prior: never re-posted
        if action in ("include_once", "include") and not _enters_report(
                row.get("confidence")):
            continue                          # sub-70: never enters the report
        body = _thread_body(row.get("severity"), row.get("one_liner"), None,
                            row.get("confidence"),
                            ", ".join(row.get("corroboration", {})
                                      .get("agents", [])))
        if action in ("include_once", "include"):
            if row["locus"]["file"] is not None:
                thread(row.get("severity"), row.get("confidence"),
                       {"file_path": row["file"],
                        "line_number": row["locus"]["lines"][0],
                        "body": body})
            else:
                notes.append({"body": body})  # anchorless (MR-process) locus
        elif action == "reply_on_thread":
            replies.append({"reply_to_thread_id": row["thread_id"],
                            "body": body})

    for row in worklist.get("judgment", []):
        kind, preset = row.get("kind"), row.get("preset_outcome")
        if (kind in ("disagreement", "cross_finding")
                or preset == "keep_each_perspective"):
            included = row["findings"]        # present_both / keep_both / keep each
        else:
            included = []                     # include_or_drop (sub-threshold)
        for f in included:
            if not _enters_report(f.get("confidence")):
                continue                      # sub-70: never enters the report
            body = _thread_body(f.get("severity"), f.get("one_liner"),
                                f.get("evidence_snippet"), f.get("confidence"),
                                f.get("agent"))
            lr = f.get("line_range")
            if f.get("file") is None or not (isinstance(lr, list) and lr):
                notes.append({"body": body})  # anchorless judgment finding
            else:
                thread(f.get("severity"), f.get("confidence"),
                       {"file_path": f["file"], "line_number": lr[0],
                        "body": body})

    decorated.sort(key=lambda t: (-t[0], -t[1]))   # stable: ties keep row order
    return [e for _, _, e in decorated] + replies + notes


def make_stress_findings():
    """Deterministic 88-finding stress set across the three agents: 20
    conflict pairs (identical text, critical 95 vs minor 72), 10
    cross-category pairs (logic vs a03-injection, 80/80), 10 same-locus
    distinct pairs (disjoint token sets, 75/78), 3 near-dup merged pairs
    (the proven Jaccard-0.75 text pattern, 88/90) and 2 confident singletons
    (90). Nothing falls below 70 — load_findings drops < 50 outright and
    sub-70 representatives change cluster reasons opaquely. Returns the
    three findings arrays [codebase, security, analyst]."""
    cb, sec, ana = [], [], []

    def f(agent, file, lo, hi, category, severity, confidence, one_liner,
          evidence):
        return {"agent": agent, "file": file, "line_range": [lo, hi],
                "category": category, "severity": severity,
                "confidence": confidence, "one_liner": one_liner,
                "evidence": evidence}

    for i in range(20):                       # conflict pairs -> judgment rows
        one = "Unbounded retry loop on transient failure"
        ev = "c%d.py:10 retries forever without a cap on attempts" % i
        cb.append(f("codebase", "src/stress/c%d.py" % i, 10, 12, "logic",
                    "critical", 95, one, ev))
        sec.append(f("security", "src/stress/c%d.py" % i, 10, 12, "logic",
                     "minor", 72, one, ev))
    for i in range(10):                       # cross-category pairs
        one = "User input reaches template render unsanitized"
        ev = ("x%d.py:20 passes raw request param directly into the template "
              "context" % i)
        cb.append(f("codebase", "src/stress/x%d.py" % i, 20, 22, "logic",
                    "important", 80, one, ev))
        sec.append(f("security", "src/stress/x%d.py" % i, 20, 22,
                     "a03-injection", "important", 80, one, ev))
    for i in range(10):                       # same-locus distinct pairs
        cb.append(f("codebase", "src/stress/d%d.py" % i, 30, 32, "logic",
                    "important", 75, "Race condition in async handler",
                    "Two concurrent requests mutate shared cache without a "
                    "lock at this call site"))
        sec.append(f("security", "src/stress/d%d.py" % i, 31, 33, "logic",
                     "important", 78, "Hardcoded fallback URL ignores config",
                     "Endpoint address is a string literal instead of "
                     "reading the settings object"))
    for file, a1, a2 in (("src/stress/n0.py", "codebase", "security"),
                         ("src/stress/n1.py", "codebase", "analyst"),
                         ("src/stress/n2.py", "security", "analyst")):
        one = "Missing None guard before dict access"
        base = os.path.basename(file)
        pools = {"codebase": cb, "security": sec, "analyst": ana}
        pools[a1].append(f(a1, file, 40, 42, "logic", "important", 88, one,
                           "%s:41 calls cfg.get('x') and dereferences without "
                           "a None check" % base))
        pools[a2].append(f(a2, file, 41, 43, "logic", "important", 90, one,
                           "%s:41 cfg.get('x') dereferences without a None "
                           "check when backend omits key" % base))
    cb.append(f("codebase", "src/stress/s0.py", 50, 51, "logic", "important",
                90, "Unclosed file handle on error path",
                "s0.py:50 opens without a context manager on the fallback "
                "branch"))
    ana.append(f("analyst", "src/stress/s1.py", 50, 51, "logic", "important",
                 90, "Unclosed file handle on error path",
                 "s1.py:50 opens without a context manager on the fallback "
                 "branch"))
    return [cb, sec, ana]


# ── local chain helpers (sibling conventions; no cross-test imports) ────────


def tmp_dir(testcase):
    d = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


def fixture(name):
    return os.path.join(FIXTURES, name)


def three_fixture_paths():
    return [fixture(n) for n in THREE_FINDINGS]


def write_findings(d, name, agent, findings):
    """Write one validator-shaped findings file (T1 shape)."""
    path = os.path.join(d, name)
    obj = {"agent": agent, "report": os.path.join(d, name + ".report.md"),
           "findings": findings, "anomalies": [], "passthrough": False}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def run_chain(testcase, findings_paths, prior=None):
    """Run the REAL chain in a temp dir: consolidate (optionally --prior)
    then adjudicate (--clusters .../clusters.json --findings <same files>).
    Returns (worklist_path, adjudicate_proc)."""
    d = tmp_dir(testcase)
    out_dir = os.path.join(d, "cons")
    args = [sys.executable, CONSOLIDATOR, "--findings", *findings_paths,
            "--out-dir", out_dir]
    if prior is not None:
        args += ["--prior", prior]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("consolidate failed: %s" % proc.stderr)
    worklist = os.path.join(d, "adjudication_worklist.json")
    proc = subprocess.run(
        [sys.executable, ADJUDICATOR,
         "--clusters", os.path.join(out_dir, "clusters.json"),
         "--findings", *findings_paths, "--out", worklist],
        capture_output=True, text=True, timeout=60)
    return worklist, proc


def realistic_chain(testcase):
    """The e2e chain over the T1 fixture inputs (retrospective prior)."""
    return run_chain(testcase, three_fixture_paths(), fixture("prior.json"))


def realistic_worklist(testcase):
    path, proc = realistic_chain(testcase)
    if proc.returncode != 0:
        raise AssertionError("adjudicate failed: %s" % proc.stderr)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def serialize_payloads(payloads):
    """Golden byte shape: indent=2, ensure_ascii=False, trailing newline."""
    return json.dumps(payloads, indent=2, ensure_ascii=False) + "\n"


def run_poster_dry(payloads_path):
    """The SC-3 round trip: the UNTOUCHED poster in --dry-run (no token)."""
    return subprocess.run(
        [sys.executable, POSTER, "--mr", "1", "--project", "g/x/y",
         "--findings-json", payloads_path, "--skip-summary", "--dry-run"],
        capture_output=True, text=True, timeout=60)


def stdout_json_line(proc):
    """Dry-run prints DRY-RUN lines before the counts line; exactly one
    stdout line is JSON and it is the counts object."""
    json_lines = [ln for ln in proc.stdout.splitlines()
                  if ln.strip() and ln.startswith("{")]
    if len(json_lines) != 1:
        raise ValueError("expected exactly one stdout JSON line, got %d: %r"
                         % (len(json_lines), proc.stdout[:400]))
    return json.loads(json_lines[0])


# ── tests ───────────────────────────────────────────────────────────────────


class TestE2E(unittest.TestCase):

    def test_turn_model_math(self):
        self.assertEqual(turns(0), 3)
        self.assertEqual(turns(5), 3)
        self.assertEqual(turns(50), 12)
        self.assertEqual(turns(51), 13)

    def test_realistic_fixture_within_budget(self):
        worklist = realistic_worklist(self)
        j = len(worklist["judgment"])
        self.assertEqual(j, 5)
        self.assertEqual(turns(5, 5), 3)
        self.assertLessEqual(turns(j), 12)

    def test_stress_fixture_within_budget(self):
        arrays = make_stress_findings()
        self.assertEqual(sum(len(a) for a in arrays), 88)
        for a in arrays:
            for finding in a:
                self.assertTrue(70 <= finding["confidence"] <= 95,
                                "stress confidence out of the 70-95 band: %r"
                                % finding)
        d = tmp_dir(self)
        paths = [write_findings(d, "%s.findings.json" % agent, agent, arr)
                 for agent, arr in zip(("codebase", "security", "analyst"),
                                       arrays)]
        path, proc = run_chain(self, paths)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(path, encoding="utf-8") as fh:
            worklist = json.load(fh)
        self.assertEqual(len(worklist["judgment"]), 40)
        self.assertEqual(sum(1 for r in worklist["judgment"]
                             if r["kind"] == "disagreement"), 20)
        self.assertEqual(sum(1 for r in worklist["judgment"]
                             if r["kind"] == "cross_finding"), 10)
        self.assertEqual(sum(1 for r in worklist["judgment"]
                             if r["kind"] == "validity"), 10)
        self.assertEqual(len(worklist["auto_decided"]), 5)
        self.assertEqual(turns(40, 5), 10)
        self.assertLessEqual(turns(40), 12)

    def test_poster_round_trip_realistic(self):
        payloads = compose_post_payloads(realistic_worklist(self))
        path = os.path.join(tmp_dir(self), "payloads.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(serialize_payloads(payloads))
        r = run_poster_dry(path)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = stdout_json_line(r)
        self.assertEqual(out["threads"], 11)
        self.assertEqual(out["replies"], 1)
        self.assertEqual(out["notes"], 1)
        self.assertEqual(out["failures"], 0)
        self.assertTrue(out["dry_run"])

    def test_payloads_golden(self):
        composed = serialize_payloads(
            compose_post_payloads(realistic_worklist(self)))
        if os.environ.get("OMNIFORGE_REGEN_GOLDENS") == "1":
            with open(GOLDEN_PAYLOADS, "w", encoding="utf-8") as fh:
                fh.write(composed)
        with open(GOLDEN_PAYLOADS, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), composed)

    def test_worklist_matches_golden(self):
        path, proc = realistic_chain(self)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(path, encoding="utf-8") as fh:
            got = fh.read()
        with open(GOLDEN_WORKLIST, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), got)

    def test_sub70_findings_never_enter_payloads(self):
        """Pin the Phase 5 final-report gate: a finding with confidence 60
        produces NO payload (auto include path and included judgment
        findings alike) while its >= 70 siblings still post."""
        worklist = {
            "auto_decided": [
                {"action": "include", "confidence": 60,
                 "severity": "important", "one_liner": "Sub-70 auto finding",
                 "corroboration": {"agents": ["codebase"]},
                 "file": "src/gate.py",
                 "locus": {"file": "src/gate.py", "lines": [3, 4]}},
                {"action": "include", "confidence": 90,
                 "severity": "important", "one_liner": "Confident auto sibling",
                 "corroboration": {"agents": ["codebase"]},
                 "file": "src/ok.py",
                 "locus": {"file": "src/ok.py", "lines": [5, 6]}},
            ],
            "judgment": [
                {"kind": "disagreement",
                 "preset_outcome": "needs_human_judgment_dual_perspective",
                 "findings": [
                     {"agent": "codebase", "severity": "critical",
                      "confidence": 95, "category": "logic",
                      "file": "src/pair.py", "line_range": [10, 12],
                      "one_liner": "High-confidence perspective",
                      "evidence_snippet": "evidence for the 95 side"},
                     {"agent": "security", "severity": "minor",
                      "confidence": 60, "category": "logic",
                      "file": "src/pair.py", "line_range": [10, 12],
                      "one_liner": "Sub-70 perspective",
                      "evidence_snippet": "evidence for the 60 side"},
                 ]},
            ],
        }
        payloads = compose_post_payloads(worklist)
        self.assertEqual([p["file_path"] for p in payloads],
                         ["src/pair.py", "src/ok.py"])   # severity rank first
        dumped = json.dumps(payloads)
        self.assertNotIn("Sub-70 auto finding", dumped)
        self.assertNotIn("Sub-70 perspective", dumped)


class TestScratchCopy(unittest.TestCase):
    """AC-8 (SC-5): with omni_adjudicate.py deleted from a scratch copy of
    the plugin tree, the pre-existing suite stays green — nothing outside
    SKILL.md and the new adjudication tests references the script."""

    def test_preexisting_suite_green_without_adjudicator(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        dst = os.path.join(d, "omniforge")
        shutil.copytree(PLUGIN_ROOT, dst,
                        ignore=shutil.ignore_patterns("__pycache__",
                                                      ".pytest_cache"))
        os.remove(os.path.join(dst, "skills", "omnireview-gitlab", "scripts",
                               "omni_adjudicate.py"))
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q",
             "--ignore=tests/test_omni_adjudicate.py",
             "--ignore=tests/test_omni_adjudicate_e2e.py"],
            cwd=dst, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0,
                         "suite in the scratch copy failed:\n%s\n%s"
                         % (r.stdout[-2000:], r.stderr[-2000:]))
        self.assertIn("passed", r.stdout)


if __name__ == "__main__":
    unittest.main()
