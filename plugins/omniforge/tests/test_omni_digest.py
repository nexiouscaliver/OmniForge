"""Contract tests for the Phase-1 context digest (omni_digest.py).

The digest is invoked as a SUBPROCESS (exit codes + the one-JSON-line stdout
contract are exercised in a real process; same convention as
test_omni_consolidate.py). The retrospective round-trip test drives BOTH the
digest and the consolidator as subprocesses, wiring the digest's
prior-findings output into omni_consolidate.py --prior.

Pinned semantics (spec D8 + W3 locked contract): bot-authored artifacts
(OmniForge summary notes, posted finding-thread bodies, verdict tokens,
file:line anchors, thread IDs, resolved state) are carried VERBATIM — never
summarized, never truncated; ONLY human prose is capped (500 chars = 400 head
+ marker + 60 tail) and re-carried diffs are reduced to hunk headers +
per-file counts; overflow beyond the 8,000-char budget drops the OLDEST
threads' prose while thread ID + resolved state remain; all five
bad-discussions modes degrade (exit 0 + one stderr warning +
retrospective: false) — never a hard failure.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

DIGEST = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
    "skills", "omnireview-gitlab", "scripts", "omni_digest.py"))
CONSOLIDATOR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
    "skills", "omnireview-gitlab", "scripts", "omni_consolidate.py"))

PROSE_CAP, HEAD, TAIL = 500, 400, 60


def tmp_dir(testcase):
    d = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


def write_json(d, name, obj):
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def stdout_json(proc):
    """stdout must be exactly one JSON line; parse and return it."""
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if len(lines) != 1:
        raise ValueError("expected exactly one stdout JSON line, got %d: %r" % (
            len(lines), proc.stdout[:400]))
    return json.loads(lines[0])


def run_digest(mr_path, discussions_path=None, out_dir=None, out=None,
               prior_out=None, timeout=60):
    args = [sys.executable, DIGEST, mr_path]
    if discussions_path is not None:
        args.append(discussions_path)
    if out_dir is not None:
        args += ["--out-dir", out_dir]
    if out is not None:
        args += ["--out", out]
    if prior_out is not None:
        args += ["--prior-out", prior_out]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


# --- fixtures ---------------------------------------------------------

DIFF_TEXT = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -40,6 +40,9 @@ def handler(cfg):
     context line
+    if cfg is None:
+        return None
+    return cfg.get("x")
     more context
diff --git a/src/net.py b/src/net.py
index 3333333..4444444 100644
--- a/src/net.py
+++ b/src/net.py
@@ -10,4 +10,4 @@ def fetch(url):
     ctx
-    old_endpoint = "http://hardcoded"
+    new_endpoint = url
     tail
"""

BOT_FINDING_NOTE = """**Important** — Missing null check before dict access

`src/app.py:42` calls `cfg.get('x')` and dereferences without a None check.

Confidence: 82/100 | Found by: Codebase Reviewer (OmniForge)"""

BOT_SUMMARY_NOTE = """## OmniForge Report: !21 — Add widget API

### Verdict: APPROVE_WITH_FIXES

### Summary
Solid change; one important fix needed before merge.
"""


def mr_data(**overrides):
    d = {
        "success": True,
        "mr_id": 21,
        "title": "Add widget API",
        "description": "Adds the widget endpoint.",
        "comments": "C" * 300,
        "diff": DIFF_TEXT,
        "files_changed": ["src/app.py", "src/net.py"],
    }
    d.update(overrides)
    return d


def thread(tid, body, replies=(), file_path=None, line_number=None,
           resolved=False, created_at="2026-09-01T10:00:00Z",
           ttype="inline"):
    """One fetch_mr_discussions-shaped thread (MCP envelope's item shape)."""
    return {
        "id": tid,
        "resolvable": file_path is not None,
        "resolved": resolved,
        "type": ttype if file_path is not None else "general",
        "file_path": file_path,
        "line_number": line_number,
        "body": body,
        "author": "reviewer_bot",
        "created_at": created_at,
        "replies": [
            {"author": a, "body": b, "created_at": ca}
            for a, b, ca in replies
        ],
    }


def discussions_payload(threads):
    return {"success": True, "mr_id": 21, "discussions": list(threads),
            "total": len(threads), "unresolved": 0, "resolved": 0}


def read_file(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


class TestOmniDigest(unittest.TestCase):
    # --- AC-T5.1: bot artifacts byte-verbatim ---------------------------

    def test_bot_artifacts_verbatim(self):
        d = tmp_dir(self)
        mr = write_json(d, "mr.json", mr_data())
        disc = write_json(d, "disc.json", discussions_payload([
            thread("abc123", BOT_FINDING_NOTE, file_path="src/app.py",
                   line_number=42, resolved=False,
                   created_at="2026-09-01T10:00:00Z"),
            thread("sumry01", BOT_SUMMARY_NOTE,
                   created_at="2026-09-01T11:00:00Z"),
        ]))
        out = os.path.join(d, "out")
        proc = run_digest(mr, disc, out_dir=out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")            # success mode: no warnings
        digest = read_file(out, "digest.md")
        # bot-authored bodies ride byte-identical (fixture source bytes)
        self.assertIn(BOT_FINDING_NOTE, digest)
        self.assertIn(BOT_SUMMARY_NOTE, digest)
        # thread id, file:line anchor, resolved state survive verbatim
        self.assertIn("abc123", digest)
        self.assertIn("src/app.py:42", digest)
        self.assertIn("— unresolved —", digest)
        st = stdout_json(proc)
        self.assertTrue(st["retrospective"])
        self.assertEqual(st["prior_count"], 2)
        self.assertEqual(st["threads_total"], 2)

    # --- AC-T5.2: human prose cap + exact marker ------------------------

    def test_human_prose_truncation_marker(self):
        long_note = "H" * HEAD + "M" * 240 + "T" * TAIL     # 700 chars
        assert len(long_note) == 700
        marker = "\n[…truncated 240 chars…]\n"
        expected = "H" * HEAD + marker + "T" * TAIL
        at_cap = "B" * PROSE_CAP                            # 500: untouched
        d = tmp_dir(self)
        mr = write_json(d, "mr.json", mr_data())
        disc = write_json(d, "disc.json", discussions_payload([
            thread("t1", long_note,
                   replies=[("human", at_cap, "2026-09-01T10:05:00Z")]),
        ]))
        out = os.path.join(d, "out")
        proc = run_digest(mr, disc, out_dir=out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        digest = read_file(out, "digest.md")
        self.assertIn(expected, digest)
        self.assertNotIn("M" * 10, digest)                  # middle dropped
        self.assertIn(at_cap, digest)                       # <= 500 untouched
        st = stdout_json(proc)
        self.assertEqual(st["threads_truncated"], 1)
        self.assertFalse(st["retrospective"])               # human prose only

    # --- AC-T5.3: diff re-carry = hunk headers + counts only ------------

    def test_diff_recarry_hunk_headers_only(self):
        d = tmp_dir(self)
        mr = write_json(d, "mr.json", mr_data())
        disc = write_json(d, "disc.json", discussions_payload([
            thread("t1", "looks fine to me", file_path="src/app.py",
                   line_number=41),
        ]))
        out = os.path.join(d, "out")
        proc = run_digest(mr, disc, out_dir=out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        digest = read_file(out, "digest.md")
        # hunk headers + per-file +N/-M counts present
        self.assertIn("@@ -40,6 +40,9 @@", digest)
        self.assertIn("@@ -10,4 +10,4 @@", digest)
        self.assertIn("| src/app.py | +3/-0 |", digest)
        self.assertIn("| src/net.py | +1/-1 |", digest)
        # diff BODIES never re-carried
        self.assertNotIn('return cfg.get("x")', digest)
        self.assertNotIn("old_endpoint", digest)
        self.assertNotIn("http://hardcoded", digest)

    # --- AC-T5.4a: prior-findings shape ----------------------------------

    def test_prior_findings_shape(self):
        d = tmp_dir(self)
        mr = write_json(d, "mr.json", mr_data())
        disc = write_json(d, "disc.json", discussions_payload([
            thread("loc1", BOT_FINDING_NOTE, file_path="src/app.py",
                   line_number=42, resolved=False,
                   created_at="2026-09-01T10:00:00Z"),
            thread("nopos", BOT_SUMMARY_NOTE, resolved=True,
                   created_at="2026-09-01T09:00:00Z"),   # no position data
            thread("human1", "human question, no bot note",
                   created_at="2026-09-01T12:00:00Z"),
        ]))
        out = os.path.join(d, "out")
        proc = run_digest(mr, disc, out_dir=out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(out, "prior-findings.json"),
                  encoding="utf-8") as fh:
            prior = json.load(fh)
        self.assertTrue(prior["retrospective"])
        self.assertEqual(len(prior["prior_findings"]), 2)  # human thread out
        by_id = {p["thread_id"]: p for p in prior["prior_findings"]}
        self.assertEqual(sorted(by_id), ["loc1", "nopos"])
        for p in prior["prior_findings"]:
            self.assertEqual(sorted(p),
                             ["body", "file_path", "line_number",
                              "resolved", "state", "thread_id"])
        loc1 = by_id["loc1"]
        self.assertEqual(loc1["file_path"], "src/app.py")
        self.assertEqual(loc1["line_number"], 42)
        self.assertEqual(loc1["body"], BOT_FINDING_NOTE)   # verbatim body
        self.assertFalse(loc1["resolved"])
        self.assertEqual(loc1["state"], "open")
        nopos = by_id["nopos"]                              # no locus: nulls
        self.assertIsNone(nopos["file_path"])
        self.assertIsNone(nopos["line_number"])
        self.assertTrue(nopos["resolved"])
        self.assertEqual(nopos["state"], "resolved")
        st = stdout_json(proc)
        self.assertTrue(st["retrospective"])
        self.assertEqual(st["prior_count"], 2)

    # --- AC-T5.4b: round-trip into the consolidator's --prior -----------

    def test_prior_findings_roundtrip_into_consolidator(self):
        d = tmp_dir(self)
        mr = write_json(d, "mr.json", mr_data())
        disc = write_json(d, "disc.json", discussions_payload([
            thread("abc123", BOT_FINDING_NOTE, file_path="src/app.py",
                   line_number=42),
        ]))
        out1 = os.path.join(d, "digest_out")
        prior_custom = os.path.join(d, "prior_findings.json")
        proc = run_digest(mr, disc, out_dir=out1, prior_out=prior_custom)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = stdout_json(proc)
        self.assertEqual(st["prior_out"], prior_custom)
        self.assertTrue(os.path.isfile(prior_custom))

        finding = {
            "agent": "codebase", "file": "src/app.py", "line_range": [42, 44],
            "category": "logic", "severity": "important", "confidence": 82,
            "one_liner": "Missing None guard before dict access",
            "evidence": "app.py:43 calls cfg.get('x') and dereferences "
                        "without a None check",
        }
        fpath = write_json(d, "codebase.findings.json", {
            "agent": "codebase", "report": "r.md", "findings": [finding],
            "anomalies": [], "passthrough": False})
        out2 = os.path.join(d, "cons_out")
        proc2 = subprocess.run(
            [sys.executable, CONSOLIDATOR, "--findings", fpath,
             "--prior", prior_custom, "--out-dir", out2],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        with open(os.path.join(out2, "clusters.json"), encoding="utf-8") as fh:
            clusters = json.load(fh)
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        self.assertEqual(c["prior_match"],
                         {"thread_id": "abc123", "state": "open"})
        self.assertIn("already_adjudicated", c["worklist_reasons"])
        st2 = stdout_json(proc2)
        self.assertEqual(st2["already_adjudicated"], 1)

    # --- overflow: oldest prose dropped, machine fields remain ----------

    def test_overflow_drops_oldest_prose(self):
        threads = []
        for i in range(20):                       # t00 oldest ... t19 newest
            body = ("PROSE-%02d " % i) + "p" * (PROSE_CAP - 20)
            threads.append(thread("t%02d" % i, body, resolved=False,
                                  created_at="2026-09-01T10:%02d:00Z" % i))
        d = tmp_dir(self)
        mr = write_json(d, "mr.json", mr_data(diff="", comments=""))
        disc = write_json(d, "disc.json", discussions_payload(threads))
        out = os.path.join(d, "out")
        proc = run_digest(mr, disc, out_dir=out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        digest = read_file(out, "digest.md")
        self.assertLessEqual(len(digest), 8000)    # budget enforced
        self.assertIn("PROSE-19", digest)          # newest prose survives
        self.assertNotIn("PROSE-00", digest)       # oldest prose dropped
        self.assertNotIn("PROSE-05", digest)
        self.assertIn("t00", digest)               # thread id + state remain
        self.assertIn("— unresolved —", digest)
        self.assertIn("t19", digest)

    # --- degrade paths: exit 0 + ONE stderr warning + retrospective off -

    def test_digest_degrades_on_bad_discussions_input(self):
        d = tmp_dir(self)
        mr = write_json(d, "mr.json", mr_data())

        bad_json = os.path.join(d, "bad.json")
        with open(bad_json, "w", encoding="utf-8") as fh:
            fh.write("{not json at all")

        cases = {
            "missing_arg": None,
            "absent_file": os.path.join(d, "does_not_exist.json"),
            "invalid_json": bad_json,
            "success_not_true": write_json(d, "failed.json",
                                           {"success": False,
                                            "error": "api_error"}),
            "missing_discussions_key": write_json(d, "nokey.json",
                                                  {"success": True,
                                                   "total": 0}),
        }
        for name, disc in cases.items():
            with self.subTest(case=name):
                out = os.path.join(d, "out_" + name)
                proc = run_digest(mr, disc, out_dir=out)
                self.assertEqual(proc.returncode, 0,
                                 "%s must degrade, not fail" % name)
                warnings = [l for l in proc.stderr.splitlines() if l.strip()]
                self.assertEqual(len(warnings), 1,
                                 "exactly one stderr warning line")
                self.assertIn("omni_digest", warnings[0])
                st = stdout_json(proc)
                self.assertFalse(st["retrospective"])
                self.assertEqual(st["prior_count"], 0)
                digest = read_file(out, "digest.md")
                self.assertIn("# MR digest — retrospective: false", digest)
                with open(os.path.join(out, "prior-findings.json"),
                          encoding="utf-8") as fh:
                    prior = json.load(fh)
                self.assertFalse(prior["retrospective"])
                self.assertEqual(prior["prior_findings"], [])
        # degrade digest still carries fetch_mr_data's embedded comments
        out = os.path.join(d, "out_missing_arg")
        digest = read_file(out, "digest.md")
        self.assertIn("C" * 300, digest)


if __name__ == "__main__":
    unittest.main()
