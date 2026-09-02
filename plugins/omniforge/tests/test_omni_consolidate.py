"""Contract tests for the deterministic consolidator (omni_consolidate.py).

The consolidator is invoked as a SUBPROCESS (exit codes + the one-JSON-line
stdout contract are exercised in a real process; same convention as
test_omni_validate_findings.py); pure predicate checks load the module
in-process via importlib. Stdlib only — no pytest import — so the module runs
identically under bare unittest and pytest.

Pinned semantics (spec D3 + W3 locked contract): merge ONLY near-identical
findings (same normalized file + overlapping line_range + same normalized
category + token-Jaccard >= 0.30 on one_liner+evidence); everything else is
clustered with per-agent entries VERBATIM; corroboration is metadata only;
NO confidence arithmetic anywhere (output confidences are multiset-equal to
inputs); one 5-section worklist consumed in a single pass; prior locus
matches mark clusters already_adjudicated (open -> reply on the recorded
thread, resolved -> skip re-posting).
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

CONSOLIDATOR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
    "skills", "omnireview-gitlab", "scripts", "omni_consolidate.py"))

# Worklist section headings, in mandated single-pass consumption order.
SEC_ALREADY = "Already adjudicated (carry forward as-is — never re-adjudicated)"
SEC_CONFLICT = "Needs Human Judgment (conflict — both perspectives verbatim)"
SEC_CROSS = "Cross-category, same locus (keep both)"
SEC_SAME_LOCUS = "Same locus, distinct findings (not near-identical — keep each perspective)"
SEC_SUB = "Sub-threshold observations (confidence 50–69)"
SEC_AUTO = "Auto clusters (no adjudication needed — straight into the Phase 5 report)"
SECTION_ORDER = [SEC_ALREADY, SEC_CONFLICT, SEC_CROSS, SEC_SAME_LOCUS, SEC_SUB, SEC_AUTO]


def load_consolidator_module():
    """In-process loader for pure-function checks (jaccard / predicate)."""
    spec = importlib.util.spec_from_file_location("omni_consolidate_under_test",
                                                  CONSOLIDATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tmp_dir(testcase):
    d = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


def finding(**overrides):
    """A minimal well-formed codebase finding; tests override fields.

    Token set (after STOPWORDS removal) is exactly:
      {missing, none, guard, before, dict, access, app, py, 43, calls, cfg,
       get, x, dereferences, without, check}  (16 tokens)
    """
    f = {
        "agent": "codebase",
        "file": "src/app.py",
        "line_range": [42, 44],
        "category": "logic",
        "severity": "important",
        "confidence": 82,
        "one_liner": "Missing None guard before dict access",
        "evidence": "app.py:43 calls cfg.get('x') and dereferences without a None check",
    }
    f.update(overrides)
    return f


def near_dup_security(**overrides):
    """Near-duplicate of finding() from the security agent.

    Token set: the 16 above minus {calls} plus {when, backend, omits, key}
    = 19 tokens; intersection 15, union 20 -> Jaccard 0.75 (>= 0.30, merges).
    """
    f = {
        "agent": "security",
        "file": "src/app.py",
        "line_range": [43, 45],
        "category": "logic",
        "severity": "important",
        "confidence": 88,
        "one_liner": "Missing None guard before dict access",
        "evidence": "app.py:43 cfg.get('x') dereferences without a None check when backend omits key",
    }
    f.update(overrides)
    return f


def distinct_one(**overrides):
    """Same-locus DIFFERENT-substance finding (token set disjoint from
    distinct_two): {race, condition, async, handler, two, concurrent,
    requests, mutate, shared, cache, without, lock, call, site}."""
    f = {
        "agent": "codebase",
        "file": "src/net.py",
        "line_range": [10, 12],
        "category": "logic",
        "severity": "important",
        "confidence": 75,
        "one_liner": "Race condition in async handler",
        "evidence": "Two concurrent requests mutate shared cache without a lock at this call site",
    }
    f.update(overrides)
    return f


def distinct_two(**overrides):
    """Token set disjoint from distinct_one: {hardcoded, fallback, url,
    ignores, config, endpoint, address, string, literal, instead, reading,
    settings, object} -> pairwise Jaccard 0 (never merges)."""
    f = {
        "agent": "security",
        "file": "src/net.py",
        "line_range": [11, 13],
        "category": "logic",
        "severity": "important",
        "confidence": 78,
        "one_liner": "Hardcoded fallback URL ignores config",
        "evidence": "Endpoint address is a string literal instead of reading the settings object",
    }
    f.update(overrides)
    return f


def write_findings(d, name, agent, findings):
    """Write one T1-shaped validated findings file."""
    path = os.path.join(d, name)
    obj = {"agent": agent, "report": os.path.join(d, name + ".report.md"),
           "findings": findings, "anomalies": [], "passthrough": False}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def write_prior(d, name, prior_findings):
    """Write a prior-findings file: list of entries carrying
    {thread_id, resolved (bool), file_path, line_number, body}."""
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"prior_findings": prior_findings, "retrospective": True},
                  fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def run_consolidate(finding_paths, out_dir, prior=None, threshold=None,
                    similarity=None, timeout=60):
    args = [sys.executable, CONSOLIDATOR,
            "--findings", *finding_paths, "--out-dir", out_dir]
    if prior is not None:
        args += ["--prior", prior]
    if threshold is not None:
        args += ["--threshold", str(threshold)]
    if similarity is not None:
        args += ["--similarity", str(similarity)]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def stdout_json(proc):
    """stdout must be exactly one JSON line; parse and return it."""
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if len(lines) != 1:
        raise ValueError("expected exactly one stdout JSON line, got %d: %r" % (
            len(lines), proc.stdout[:400]))
    return json.loads(lines[0])


def read_out(out_dir):
    with open(os.path.join(out_dir, "clusters.json"), encoding="utf-8") as fh:
        clusters = json.load(fh)
    with open(os.path.join(out_dir, "worklist.md"), encoding="utf-8") as fh:
        worklist = fh.read()
    return clusters, worklist


def sections(worklist):
    """Map top-level '## ' section title -> body text (insertion ordered)."""
    secs, cur = {}, None
    for line in worklist.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            cur = line[3:].strip()
            secs[cur] = []
        elif cur is not None:
            secs[cur].append(line)
    return {k: "\n".join(v) for k, v in secs.items()}


def dump_verbatim(entry):
    """Serialize an entry exactly as clusters.json does (key order kept)."""
    return json.dumps(entry, ensure_ascii=False)


class TestOmniConsolidate(unittest.TestCase):
    # --- merge predicate (AC-T2.1 / AC-T2.2) ---

    def test_near_identical_pair_merges(self):
        d = tmp_dir(self)
        f1 = write_findings(d, "codebase.findings.json", "codebase", [finding()])
        f2 = write_findings(d, "security.findings.json", "security",
                            [near_dup_security()])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1, f2], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, _ = read_out(out)
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        self.assertTrue(c["merged"])
        self.assertEqual(c["agents"], ["codebase", "security"])
        self.assertEqual(sorted(map(dump_verbatim, c["entries"])),
                         sorted(map(dump_verbatim, [finding(), near_dup_security()])))
        # representative = highest confidence (88, security)
        self.assertEqual(c["locus"]["anchor"], "src/app.py:43")
        self.assertEqual(c["locus"]["lines"], [42, 45])
        st = stdout_json(proc)
        self.assertEqual(st["clusters"], 1)
        self.assertEqual(st["merged"], 1)
        self.assertEqual(st["auto_post"], 1)        # no reasons, rep 88 >= 70

    def test_same_locus_different_substance_not_merged(self):
        d = tmp_dir(self)
        f1 = write_findings(d, "codebase.findings.json", "codebase", [distinct_one()])
        f2 = write_findings(d, "security.findings.json", "security", [distinct_two()])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1, f2], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, _ = read_out(out)
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        self.assertFalse(c["merged"])               # same locus, J = 0
        self.assertEqual(len(c["entries"]), 2)      # both kept, verbatim
        self.assertIn("same_locus_distinct_findings", c["worklist_reasons"])
        self.assertEqual(c["posting"], "worklist")

    def test_no_confidence_arithmetic_multiset_equality(self):
        d = tmp_dir(self)
        inputs = [
            finding(), near_dup_security(),
            distinct_one(), distinct_two(),
            finding(file="src/edge.py", line_range=[7, 8], confidence=91,
                    one_liner="Unclosed file handle on error path",
                    evidence="loader.py:7 opens without a context manager on the fallback branch"),
            finding(agent="analyst", file=None, line_range=None, category="scope",
                    confidence=55, one_liner="Unrelated changes mixed into one MR",
                    evidence="Commit d41b mixes a refactor with the feature it ships"),
        ]
        f1 = write_findings(d, "codebase.findings.json", "codebase", inputs[0:3] + [inputs[4]])
        f2 = write_findings(d, "security.findings.json", "security", [inputs[1], inputs[3]])
        f3 = write_findings(d, "analyst.findings.json", "analyst", [inputs[5]])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1, f2, f3], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, _ = read_out(out)
        got = sorted(str(e["confidence"]) for c in clusters for e in c["entries"])
        want = sorted(str(f["confidence"]) for f in inputs)
        self.assertEqual(got, want)                 # byte-identical multiset

    def test_minority_finding_survives_verbatim(self):
        d = tmp_dir(self)
        solo = finding(agent="security", file="src/sec.py", line_range=[10, 10],
                       category="a02-crypto", severity="critical", confidence=93,
                       one_liner="Weak hash used for password reset token",
                       evidence="tokens.py:10 signs the reset link with md5")
        f1 = write_findings(d, "codebase.findings.json", "codebase", [finding()])
        f2 = write_findings(d, "security.findings.json", "security", [solo])
        f3 = write_findings(d, "analyst.findings.json", "analyst",
                            [near_dup_security(agent="analyst")])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1, f2, f3], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, _ = read_out(out)
        solo_clusters = [c for c in clusters if c["locus"]["file"] == "src/sec.py"]
        self.assertEqual(len(solo_clusters), 1)
        self.assertEqual(len(solo_clusters[0]["entries"]), 1)
        self.assertEqual(dump_verbatim(solo_clusters[0]["entries"][0]),
                         dump_verbatim(solo))       # 1-of-3 survives byte-identical
        self.assertEqual(solo_clusters[0]["corroboration"],
                         {"count": 1, "of": 3, "agents": ["security"]})

    def test_corroboration_metadata_only(self):
        d = tmp_dir(self)
        f1 = write_findings(d, "codebase.findings.json", "codebase", [finding()])
        f2 = write_findings(d, "security.findings.json", "security",
                            [near_dup_security()])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1, f2], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, _ = read_out(out)
        c = clusters[0]
        self.assertEqual(c["corroboration"],
                         {"count": 2, "of": 2, "agents": ["codebase", "security"]})
        # metadata ONLY: no derived score key anywhere in the corroboration
        # dict, and entry confidences are the untouched agent-assigned values.
        self.assertNotIn("confidence", c["corroboration"])
        self.assertEqual(sorted(e["confidence"] for e in c["entries"]), [82, 88])
        self.assertEqual(set(c), {"cluster_id", "locus", "agents", "corroboration",
                                  "merged", "categories", "entries", "posting",
                                  "worklist_reasons"})

    # --- worklist routing (AC-T2.5) ---

    def test_cross_category_same_locus_worklisted(self):
        d = tmp_dir(self)
        base = dict(one_liner="User input reaches template render unsanitized",
                    evidence="views.py:30 passes raw request param directly into the template context")
        a = finding(file="src/view.py", line_range=[30, 32], category="logic",
                    severity="important", confidence=80, **base)
        b = finding(agent="security", file="src/view.py", line_range=[30, 32],
                    category="a03-injection", severity="important", confidence=84, **base)
        f1 = write_findings(d, "codebase.findings.json", "codebase", [a])
        f2 = write_findings(d, "security.findings.json", "security", [b])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1, f2], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, worklist = read_out(out)
        c = clusters[0]
        self.assertFalse(c["merged"])               # category differs -> never merges
        self.assertIn("cross_category_same_locus", c["worklist_reasons"])
        self.assertEqual(c["posting"], "worklist")
        self.assertIn(c["cluster_id"], sections(worklist)[SEC_CROSS])

    def test_severity_conflict_worklisted(self):
        d = tmp_dir(self)
        base = dict(one_liner="Unbounded retry loop on transient failure",
                    evidence="client.py:77 retries forever without a cap on attempts")
        a = finding(file="src/client.py", line_range=[77, 79], category="logic",
                    severity="critical", confidence=91, **base)
        b = finding(agent="security", file="src/client.py", line_range=[77, 79],
                    category="logic", severity="minor", confidence=72, **base)
        f1 = write_findings(d, "codebase.findings.json", "codebase", [a])
        f2 = write_findings(d, "security.findings.json", "security", [b])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1, f2], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, worklist = read_out(out)
        c = clusters[0]
        self.assertIn("conflict", c["worklist_reasons"])
        self.assertEqual(c["posting"], "worklist")
        sec = sections(worklist)[SEC_CONFLICT]
        self.assertIn("Needs Human Judgment", sec)
        self.assertIn(c["cluster_id"], sec)
        # BOTH perspectives verbatim in the worklist item
        self.assertIn("critical", sec)
        self.assertIn("minor", sec)
        self.assertIn(str(91), sec)
        self.assertIn(str(72), sec)

    def test_sub_threshold_50_69_worklisted(self):
        d = tmp_dir(self)
        sub = finding(file="src/sub.py", line_range=[5, 5], confidence=55,
                      one_liner="Commit message lacks a why",
                      evidence="Commit 1a2b3c4 states what changed with no rationale")
        f1 = write_findings(d, "codebase.findings.json", "codebase", [sub])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, worklist = read_out(out)
        c = clusters[0]
        self.assertIn("sub_threshold", c["worklist_reasons"])
        self.assertEqual(c["posting"], "worklist")
        self.assertEqual(dump_verbatim(c["entries"][0]), dump_verbatim(sub))  # not deleted
        self.assertIn(c["cluster_id"], sections(worklist)[SEC_SUB])

    def test_below_50_never_surfaced(self):
        d = tmp_dir(self)
        low = finding(file="src/low.py", line_range=[12, 12], confidence=40,
                      one_liner="Typo in variable name",
                      evidence="runner.py:12 misspells receive as recieve")
        ok = finding()
        f1 = write_findings(d, "codebase.findings.json", "codebase", [low, ok])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(out, "clusters.json"), encoding="utf-8") as fh:
            clusters_text = fh.read()
        with open(os.path.join(out, "worklist.md"), encoding="utf-8") as fh:
            worklist_text = fh.read()
        self.assertNotIn("40", clusters_text)
        self.assertNotIn("40", worklist_text)

    # --- prior / retrospective guard (AC-T2.6) ---

    def test_prior_locus_match_marks_already_adjudicated(self):
        d = tmp_dir(self)
        cur = finding(file="src/app.py", line_range=[42, 44], confidence=85)
        f1 = write_findings(d, "codebase.findings.json", "codebase", [cur])
        prior = write_prior(d, "prior.json", [{
            "thread_id": "T-100", "resolved": True, "file_path": "src/app.py",
            "line_number": 43,
            "body": "**Important** — Missing None guard before dict access",
        }])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1], out, prior=prior)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, worklist = read_out(out)
        c = clusters[0]
        self.assertEqual(c["prior_match"], {"thread_id": "T-100", "state": "resolved"})
        self.assertIn("already_adjudicated", c["worklist_reasons"])
        self.assertEqual(c["posting"], "worklist")
        sec = sections(worklist)[SEC_ALREADY]
        self.assertIn(c["cluster_id"], sec)
        self.assertIn("skip re-posting", sec)
        self.assertEqual(stdout_json(proc)["already_adjudicated"], 1)

    def test_prior_state_open_reply_vs_resolved_skip(self):
        d = tmp_dir(self)
        open_f = finding(file="src/open.py", line_range=[10, 12], confidence=86,
                         one_liner="Missing rate limit on login endpoint",
                         evidence="auth.py:11 accepts unlimited attempts per source")
        res_f = finding(file="src/res.py", line_range=[20, 22], confidence=87,
                        one_liner="SQL query built by concatenation",
                        evidence="db.py:21 concatenates user input into the query text")
        f1 = write_findings(d, "codebase.findings.json", "codebase", [open_f, res_f])
        prior = write_prior(d, "prior.json", [
            {"thread_id": "T-open", "resolved": False, "file_path": "src/open.py",
             "line_number": 11, "body": "**Important** — Missing rate limit"},
            {"thread_id": "T-res", "resolved": True, "file_path": "src/res.py",
             "line_number": 21, "body": "**Important** — SQL concatenation"},
        ])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1], out, prior=prior)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, worklist = read_out(out)
        by_file = {c["locus"]["file"]: c for c in clusters}
        self.assertEqual(by_file["src/open.py"]["prior_match"],
                         {"thread_id": "T-open", "state": "open"})
        self.assertEqual(by_file["src/res.py"]["prior_match"],
                         {"thread_id": "T-res", "state": "resolved"})
        for c in clusters:
            self.assertIn("already_adjudicated", c["worklist_reasons"])
        sec = sections(worklist)[SEC_ALREADY]
        self.assertIn("reply on thread T-open", sec)      # open -> reply on recorded thread
        self.assertIn("skip re-posting", sec)             # resolved -> never re-posted
        self.assertEqual(stdout_json(proc)["already_adjudicated"], 2)

    # --- locus / tolerance (locked-contract hazard note) ---

    def test_null_file_never_merges(self):
        d = tmp_dir(self)
        a = finding(file=None, line_range=[42, 44], agent="analyst",
                    category="commit-quality", confidence=81,
                    one_liner="Fixup commit not squashed before review",
                    evidence="Commit 4b1f2e3 only edits a line from 9c0d7aa earlier here")
        b = finding(file=None, line_range=[42, 44], agent="analyst",
                    category="commit-quality", confidence=83,
                    one_liner="Fixup commit not squashed before review",
                    evidence="Commit 4b1f2e3 only edits a line from 9c0d7aa earlier here")
        f1 = write_findings(d, "analyst.findings.json", "analyst", [a, b])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, _ = read_out(out)
        self.assertEqual(len(clusters), 2)          # identical text, null file -> no merge
        for c in clusters:
            self.assertFalse(c["merged"])
            self.assertIsNone(c["locus"]["file"])
            self.assertEqual(c["locus"]["anchor"], "MR-process")

    def test_nonstring_file_category_anomaly_not_crash(self):
        d = tmp_dir(self)
        weird = finding(file=42, line_range=[42, 44], category=["logic"],
                        confidence=76)
        normal = finding(agent="security", confidence=79)
        f1 = write_findings(d, "codebase.findings.json", "codebase", [weird, normal])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)   # never crashes on .strip()
        clusters, _ = read_out(out)
        # the non-string-file finding never merges with the string-file one
        self.assertEqual(len(clusters), 2)
        weird_c = [c for c in clusters if any(e.get("file") == 42 for e in c["entries"])][0]
        self.assertEqual(weird_c["locus"]["file"], None)    # treated as null
        self.assertEqual(dump_verbatim(weird_c["entries"][0]), dump_verbatim(weird))
        self.assertGreaterEqual(stdout_json(proc)["anomalies"], 1)
        self.assertIn("not a string", proc.stderr)          # anomaly noted on stderr

    # --- determinism + CLI (AC-T2.7, D3) ---

    def test_deterministic_rerun_byte_identical(self):
        d = tmp_dir(self)
        f1 = write_findings(d, "codebase.findings.json", "codebase",
                            [finding(), distinct_one()])
        f2 = write_findings(d, "security.findings.json", "security",
                            [near_dup_security(), distinct_two()])
        prior = write_prior(d, "prior.json", [
            {"thread_id": "T-9", "resolved": True, "file_path": "src/app.py",
             "line_number": 44, "body": "**Important** — prior note"},
        ])
        out1, out2 = os.path.join(d, "out1"), os.path.join(d, "out2")
        p1 = run_consolidate([f1, f2], out1, prior=prior)
        p2 = run_consolidate([f1, f2], out2, prior=prior)
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertEqual(p2.returncode, 0, p2.stderr)
        for name in ("clusters.json", "worklist.md"):
            with open(os.path.join(out1, name), "rb") as fh:
                b1 = fh.read()
            with open(os.path.join(out2, name), "rb") as fh:
                b2 = fh.read()
            self.assertEqual(b1, b2, name)         # byte-identical re-run

    def test_worklist_ordering_single_pass(self):
        d = tmp_dir(self)
        sub = finding(file="src/a.py", line_range=[5, 5], confidence=55,
                      one_liner="Commit message lacks a why",
                      evidence="Commit 1a2b3c4 states what changed with no rationale")
        conflict_a = finding(file="src/b.py", line_range=[77, 79], severity="critical",
                             confidence=91, one_liner="Unbounded retry loop on transient failure",
                             evidence="client.py:77 retries forever without a cap on attempts")
        conflict_b = finding(agent="security", file="src/b.py", line_range=[77, 79],
                             severity="minor", confidence=72,
                             one_liner="Unbounded retry loop on transient failure",
                             evidence="client.py:77 retries forever without a cap on attempts")
        prior_f = finding(file="src/d.py", line_range=[10, 10], confidence=85,
                          one_liner="Unclosed file handle on error path",
                          evidence="loader.py:10 opens without a context manager on the fallback branch")
        cross_a = finding(file="src/e.py", line_range=[30, 32], confidence=80,
                          one_liner="User input reaches template render unsanitized",
                          evidence="views.py:30 passes raw request param into the template context")
        cross_b = finding(agent="security", file="src/e.py", line_range=[30, 32],
                          category="a03-injection", confidence=84,
                          one_liner="User input reaches template render unsanitized",
                          evidence="views.py:30 passes raw request param into the template context")
        auto = finding(agent="security", file="src/f.py", line_range=[20, 21],
                       category="a05-misconfiguration", severity="minor", confidence=85,
                       one_liner="Debug flag enabled in production config",
                       evidence="settings.py:20 sets DEBUG true unconditionally in the base environment")
        f1 = write_findings(d, "codebase.findings.json", "codebase",
                            [sub, conflict_a, distinct_one(), prior_f, cross_a])
        f2 = write_findings(d, "security.findings.json", "security",
                            [conflict_b, distinct_two(), cross_b, auto])
        prior = write_prior(d, "prior.json", [
            {"thread_id": "T-res", "resolved": True, "file_path": "src/d.py",
             "line_number": 10, "body": "**Important** — prior note"},
        ])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1, f2], out, prior=prior)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, worklist = read_out(out)
        secs = sections(worklist)
        self.assertEqual(list(secs), SECTION_ORDER)     # single-pass order pinned
        for c in clusters:                              # every worklist cluster in exactly one section
            if c["posting"] == "worklist":
                placed = [k for k, body in secs.items() if c["cluster_id"] in body
                          and k != SEC_AUTO]
                self.assertEqual(len(placed), 1, c["cluster_id"])
        # the multi-entry non-merged same-locus cluster lands in its section
        same_locus_ids = [c["cluster_id"] for c in clusters
                          if "same_locus_distinct_findings" in c["worklist_reasons"]
                          and "cross_category_same_locus" not in c["worklist_reasons"]
                          and "conflict" not in c["worklist_reasons"]]
        self.assertEqual(len(same_locus_ids), 1)
        self.assertIn(same_locus_ids[0], secs[SEC_SAME_LOCUS])
        # sub-threshold section carries its cluster
        sub_ids = [c["cluster_id"] for c in clusters
                   if "sub_threshold" in c["worklist_reasons"]]
        self.assertIn(sub_ids[0], secs[SEC_SUB])
        # auto clusters listed last, straight into the report
        auto_ids = [c["cluster_id"] for c in clusters if c["posting"] == "auto"]
        self.assertEqual(len(auto_ids), 1)
        self.assertIn(auto_ids[0], secs[SEC_AUTO])

    def test_degraded_two_agent_input_ok(self):
        d = tmp_dir(self)
        f1 = write_findings(d, "codebase.findings.json", "codebase", [finding()])
        f2 = write_findings(d, "security.findings.json", "security",
                            [near_dup_security()])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1, f2], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clusters, _ = read_out(out)
        self.assertEqual(clusters[0]["corroboration"]["of"], 2)   # N = input files

    def test_similarity_threshold_flag_respected(self):
        # token sets: {alpha1..alpha10} vs {alpha1..alpha6, beta1..beta4}
        # intersection 6, union 14 -> Jaccard 6/14 ~= 0.4286
        a = finding(file="src/sim.py", line_range=[5, 6], category="testing",
                    severity="minor", confidence=71,
                    one_liner="alpha1 alpha2 alpha3 alpha4 alpha5",
                    evidence="alpha6 alpha7 alpha8 alpha9 alpha10")
        b = finding(agent="security", file="src/sim.py", line_range=[6, 7],
                    category="testing", severity="minor", confidence=73,
                    one_liner="alpha1 alpha2 alpha3 alpha4 alpha5",
                    evidence="alpha6 beta1 beta2 beta3 beta4")
        d = tmp_dir(self)
        f1 = write_findings(d, "codebase.findings.json", "codebase", [a])
        f2 = write_findings(d, "security.findings.json", "security", [b])
        out1, out2 = os.path.join(d, "out1"), os.path.join(d, "out2")
        p1 = run_consolidate([f1, f2], out1)                       # default 0.30
        p2 = run_consolidate([f1, f2], out2, similarity=0.5)
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertEqual(p2.returncode, 0, p2.stderr)
        c1, _ = read_out(out1)
        c2, _ = read_out(out2)
        self.assertEqual(len(c1), 1)
        self.assertTrue(c1[0]["merged"])          # 0.4286 >= 0.30
        self.assertEqual(len(c2), 1)
        self.assertFalse(c2[0]["merged"])         # 0.4286 < 0.50
        mod = load_consolidator_module()
        self.assertAlmostEqual(mod.jaccard(a, b), 6 / 14, places=12)

    # --- brief-mandated cases beyond the sixteen named tests ---

    def test_empty_findings_empty_outputs_exit0(self):
        d = tmp_dir(self)
        f1 = write_findings(d, "codebase.findings.json", "codebase", [])
        f2 = write_findings(d, "security.findings.json", "security", [])
        out = os.path.join(d, "out")
        proc = run_consolidate([f1, f2], out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st["clusters"], 0)
        self.assertEqual(st["merged"], 0)
        self.assertEqual(st["worklist_items"], 0)
        self.assertEqual(st["auto_post"], 0)
        self.assertEqual(st["already_adjudicated"], 0)
        clusters, worklist = read_out(out)
        self.assertEqual(clusters, [])
        self.assertEqual(list(sections(worklist)), SECTION_ORDER)

    def test_more_than_three_findings_files_usage_error(self):
        d = tmp_dir(self)
        paths = [write_findings(d, "a%d.findings.json" % i, "codebase", [])
                 for i in range(4)]
        proc = run_consolidate(paths, os.path.join(d, "out"))
        self.assertEqual(proc.returncode, 2)      # 1-3 findings files only

    def test_invalid_findings_file_exit2(self):
        d = tmp_dir(self)
        bad = os.path.join(d, "bad.findings.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        proc = run_consolidate([bad], os.path.join(d, "out"))
        self.assertEqual(proc.returncode, 2)

    def test_unreadable_prior_degrades_not_blocks(self):
        d = tmp_dir(self)
        f1 = write_findings(d, "codebase.findings.json", "codebase", [finding()])
        bad_prior = os.path.join(d, "prior.json")
        with open(bad_prior, "w") as fh:
            fh.write("{not json")
        out = os.path.join(d, "out")
        proc = run_consolidate([f1], out, prior=bad_prior)
        self.assertEqual(proc.returncode, 0, proc.stderr)   # never blocks the run
        clusters, _ = read_out(out)
        self.assertNotIn("prior_match", clusters[0])
        self.assertGreaterEqual(stdout_json(proc)["anomalies"], 1)


if __name__ == "__main__":
    unittest.main()
