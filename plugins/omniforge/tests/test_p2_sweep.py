"""Sweep intelligence tests (P2) — pure logic over real git fixtures.

Six acceptance edge cases: rebase (non-ancestor), deleted file, rename,
cross-file fix, docs-only residual, oversized delta. Plus the citation-rule
downgrade, evidence caps, renderers (living report + breadcrumb shapes from
rev 3), skip conditions, and the provider contract (claude -p spawn is the
only live path; the direct provider API stays a stub).
"""

import contextlib
import importlib.util
import json
import os
import subprocess
import unittest
from unittest import mock

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                       "skills", "omnicheck-gitlab", "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(SCRIPTS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


omni_sweep = _load("omni_sweep")
omni_verdict = _load("omni_verdict")


def p2_repo(tmp_path, name="repo"):
    """Deterministic offline git fixture builder (p2_ slug-prefixed)."""
    path = str(tmp_path / name)
    os.makedirs(path)
    def git(*args, **kw):
        return subprocess.run(["git", "-C", path, *args], check=True,
                              capture_output=True, text=True, **kw)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    return path, git


def p2_write(path, rel, content):
    full = os.path.join(path, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)


def p2_commit(git, msg):
    git("add", "-A")
    git("commit", "-q", "-m", msg)


def p2_finding(fid, body, file_path, line, severity="important", kind="code"):
    return {"id": fid, "disposition": "not_fixed", "severity": severity,
            "kind": kind, "file_path": file_path, "line_number": line,
            "body": body, "reason": ""}


class TestDeltaPartition(unittest.TestCase):
    def test_anchor_overlap_and_symbol_match_and_residual(self):
        diff = (
            "diff --git a/src/auth.py b/src/auth.py\n"
            "index 1..2 100644\n"
            "--- a/src/auth.py\n+++ b/src/auth.py\n"
            "@@ -40,7 +40,7 @@\n"
            " ctx\n"
            "-old_claim\n"
            "+new_claim\n"
            " ctx\n"
            "diff --git a/src/other.py b/src/other.py\n"
            "index 3..4 100644\n"
            "--- a/src/other.py\n+++ b/src/other.py\n"
            "@@ -1,3 +1,4 @@\n"
            " ctx\n"
            "+validate_claim_token()\n"
            " ctx\n"
            "diff --git a/docs/new.md b/docs/new.md\n"
            "new file mode 100644\n"
            "--- /dev/null\n+++ b/docs/new.md\n"
            "@@ -0,0 +1,5 @@\n"
            "+# New doc\n"
            "+body\n"
            "+more\n"
            "+lines\n"
            "+here\n")
        findings = [
            p2_finding("f1", "claim path race", "src/auth.py", 41),
            # cross-file fix: concern about claim tokens, anchored elsewhere
            p2_finding("f2", "validate_claim_token never enforced", "src/legacy.py", 3),
        ]
        result = omni_sweep.partition_delta(diff, findings)
        rel_files = {h["file"] for h in result["relevant_hunks"]}
        self.assertIn("src/auth.py", rel_files)          # anchor overlap
        self.assertIn("src/other.py", rel_files)         # symbol match, cross-file
        self.assertNotIn("docs/new.md", rel_files)       # irrelevant -> residual
        self.assertEqual(result["residual"]["files"], ["docs/new.md"])
        self.assertEqual(result["residual"]["additions"], 5)
        self.assertEqual(result["residual"]["deletions"], 0)
        self.assertIn("docs", result["residual"]["shapes"])
        self.assertIn("new file", result["residual"]["shapes"])

    def test_shape_classification(self):
        diff = (
            "diff --git a/tests/test_x.py b/tests/test_x.py\n"
            "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n"
            "@@ -1,2 +1,3 @@\n ctx\n+assert True\n"
            "diff --git a/.gitlab-ci.yml b/.gitlab-ci.yml\n"
            "--- a/.gitlab-ci.yml\n+++ b/.gitlab-ci.yml\n"
            "@@ -1,2 +1,3 @@\n ctx\n+job: echo\n"
            "diff --git a/pyproject.toml b/pyproject.toml\n"
            "--- a/pyproject.toml\n+++ b/pyproject.toml\n"
            "@@ -1,2 +1,3 @@\n ctx\n+dep = 1\n")
        result = omni_sweep.partition_delta(diff, [])
        self.assertIn("tests-only", result["residual"]["shapes"])
        self.assertIn("ci", result["residual"]["shapes"])
        self.assertIn("manifest", result["residual"]["shapes"])


class TestReanchor(unittest.TestCase):
    DIFF = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -10,4 +10,6 @@\n"
        " ctx-a\n"
        "+inserted\n"
        "+inserted2\n"
        " ctx-b\n"
        " ctx-c\n")

    def test_line_shift(self):
        out = omni_sweep.reanchor(
            {"file_path": "app.py", "line_number": 13}, self.DIFF)
        self.assertEqual(out["status"], "reanchored")
        self.assertEqual(out["file_path"], "app.py")
        self.assertEqual(out["line_number"], 15)

    def test_line_before_hunk_unchanged(self):
        out = omni_sweep.reanchor(
            {"file_path": "app.py", "line_number": 5}, self.DIFF)
        self.assertEqual(out["status"], "reanchored")
        self.assertEqual(out["line_number"], 5)

    def test_deleted_region_is_obsolete(self):
        diff = ("diff --git a/app.py b/app.py\n"
                "--- a/app.py\n+++ b/app.py\n"
                "@@ -10,4 +10,2 @@\n"
                "-gone-1\n"
                "-gone-2\n"
                " ctx-b\n"
                " ctx-c\n")
        out = omni_sweep.reanchor(
            {"file_path": "app.py", "line_number": 11}, diff)
        self.assertEqual(out["status"], "obsolete")
        self.assertIn("deleted", out["reason"])

    def test_deleted_file_is_obsolete(self):
        diff = ("diff --git a/gone.py b/gone.py\n"
                "deleted file mode 100644\n"
                "--- a/gone.py\n+++ /dev/null\n"
                "@@ -1,3 +0,0 @@\n"
                "-a\n-b\n-c\n")
        out = omni_sweep.reanchor({"file_path": "gone.py", "line_number": 2},
                                  diff)
        self.assertEqual(out["status"], "obsolete")

    def test_rename_carries_new_path(self):
        diff = ("diff --git a/old_name.py b/new_name.py\n"
                "similarity index 95%\n"
                "rename from old_name.py\n"
                "rename to new_name.py\n"
                "--- a/old_name.py\n+++ b/new_name.py\n"
                "@@ -3,3 +3,3 @@\n"
                " ctx\n"
                "-x\n+y\n")
        out = omni_sweep.reanchor({"file_path": "old_name.py", "line_number": 3},
                                  diff)
        self.assertEqual(out["status"], "reanchored")
        self.assertEqual(out["file_path"], "new_name.py")
        self.assertEqual(out["line_number"], 3)

    def test_unmentioned_file_passes_through(self):
        out = omni_sweep.reanchor({"file_path": "untouched.py", "line_number": 7},
                                  self.DIFF)
        self.assertEqual(out["status"], "reanchored")
        self.assertEqual(out["line_number"], 7)


class TestEvidenceAndCitation(unittest.TestCase):
    DIFF = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n+++ b/a.py\n"
        "@@ -1,3 +1,3 @@\n ctx\n-old\n+new\n"
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n+++ b/b.py\n"
        "@@ -1,3 +1,3 @@\n ctx\n-old2\n+new2\n")

    def test_citation_rule_downgrades_uncited_fixed(self):
        packets = omni_sweep.build_evidence_packets(
            self.DIFF, [p2_finding("f1", "fix me", "a.py", 2)],
            anchor_map={"f1": {"status": "reanchored", "file_path": "a.py",
                               "line_number": 2}})
        verdicts = [
            {"finding_id": "f1", "verdict": "fixed", "evidence_hunk_id": "a.py:H1"},
            {"finding_id": "f1", "verdict": "fixed", "evidence_hunk_id": "zz.py:H9"},
            {"finding_id": "f1", "verdict": "fixed"},  # no citation at all
        ]
        checked = omni_sweep.apply_citation_rule(verdicts, packets)
        self.assertEqual(checked[0]["verdict"], "fixed")
        self.assertEqual(checked[1]["verdict"], "needs_judgment")
        self.assertEqual(checked[2]["verdict"], "needs_judgment")
        self.assertIn("cite", checked[2]["reason"])

    def test_evidence_caps_on_oversized_delta(self):
        big = []
        for i in range(40):
            big.append("diff --git a/f%d.py b/f%d.py" % (i, i))
            big.append("--- a/f%d.py" % i)
            big.append("+++ b/f%d.py" % i)
            big.append("@@ -1,2 +1,%d @@" % (2 + 200))
            big.append(" ctx")
            big.extend("+line-%d" % j for j in range(200))
        diff = "\n".join(big)
        packets = omni_sweep.build_evidence_packets(
            diff, [p2_finding("f1", "everything", "f0.py", 1)],
            anchor_map={"f1": {"status": "reanchored", "file_path": "f0.py",
                               "line_number": 1}})
        packet = packets[0]
        self.assertLessEqual(len(packet["candidate_hunks"]),
                             omni_sweep.MAX_HUNKS_PER_FINDING)
        for hunk in packet["candidate_hunks"]:
            self.assertLessEqual(len(hunk["lines"]),
                                 omni_sweep.MAX_HUNK_EXCERPT_LINES)
        self.assertLessEqual(len(json.dumps(packet)),
                             omni_sweep.MAX_PACKET_CHARS)

    def test_no_evidence_marker_keeps_finding_in_batch(self):
        packets = omni_sweep.build_evidence_packets(
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n ctx\n-a\n+b\n",
            [p2_finding("f1", "unrelated concern", "zz.py", 9)],
            anchor_map={"f1": {"status": "reanchored", "file_path": "zz.py",
                               "line_number": 9}})
        self.assertEqual(packets[0]["candidate_hunks"], [])
        self.assertTrue(packets[0]["no_evidence"])


class TestRenderersAndSkips(unittest.TestCase):
    VERDICTS = [
        {"finding_id": "f1", "verdict": "fixed", "evidence_hunk_id": "a.py:H1",
         "one_line": "guard added", "confidence": 90},
        {"finding_id": "f2", "verdict": "not_fixed", "one_line": "still open",
         "confidence": 88},
    ]

    def test_breadcrumb_shape_matches_rev3(self):
        crumb = omni_sweep.render_breadcrumb(
            head="abcdef1234567890", sweep_number=3,
            verdicts=self.VERDICTS,
            findings=[p2_finding("f1", "x", "a.py", 1, "important"),
                      p2_finding("f2", "y", "b.py", 2, "critical")],
            residual={"files": ["c.py", "d.py", "e.py", "f.py"],
                      "additions": 180, "deletions": 4},
            delta_review_queued=True,
            reviewed_head="1111111111111111")
        self.assertIn("**Push check complete — head `abcdef12`** (sweep #3)",
                      crumb)
        self.assertIn("- Fixed since review: 1 finding", crumb)
        self.assertIn("Still open: 1", crumb)
        self.assertIn("Critical", crumb)
        self.assertIn("New work: 4 files / 180 lines since `11111111`", crumb)
        self.assertIn("delta review queued", crumb)
        self.assertIn("Full verification table: the report above", crumb)

    def test_report_renders_table_and_residual(self):
        report = omni_sweep.render_report(
            head="abcdef1234567890", reviewed_head="1111111111111111",
            findings=[p2_finding("f1", "guard missing", "a.py", 1, "important"),
                      p2_finding("f2", "race", "b.py", 2, "critical")],
            anchor_map={}, verdicts=self.VERDICTS,
            residual={"files": ["c.py"], "additions": 10, "deletions": 2,
                      "shapes": ["code"]})
        self.assertIn("| finding | severity | kind | verdict | evidence |", report)
        self.assertIn("f2", report)
        self.assertIn("not_fixed", report)
        self.assertIn("Residual", report)
        self.assertIn("c.py", report)

    def test_skip_empty_delta(self):
        decision = omni_sweep.sweep_skip_decision(
            diff="", findings=[p2_finding("f1", "x", "a.py", 1)],
            tree_hash_equal=False)
        self.assertEqual(decision, "SKIP_EMPTY_DELTA")

    def test_skip_tree_hash_equal(self):
        decision = omni_sweep.sweep_skip_decision(
            diff="diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-a\n+b\n",
            findings=[], tree_hash_equal=True)
        self.assertEqual(decision, "SKIP_TREE_HASH_EQUAL")

    def test_docs_ci_only_residual_skips_model_call(self):
        diff = ("diff --git a/README.md b/README.md\n"
                "--- a/README.md\n+++ b/README.md\n"
                "@@ -1,2 +1,3 @@\n ctx\n+doc line\n")
        decision = omni_sweep.sweep_skip_decision(diff=diff, findings=[],
                                                  tree_hash_equal=False)
        self.assertEqual(decision, "SKIP_EVALUATION_DOCS_CI_ONLY")

    def test_nothing_open_no_residual_skips(self):
        self.assertEqual(
            omni_sweep.sweep_skip_decision(diff="", findings=[],
                                           tree_hash_equal=False),
            "SKIP_EMPTY_DELTA")

    def test_non_ancestor_marks_stale(self):
        # rev-3 rule: rebase/force-push => no re-anchoring, findings stale,
        # report-only, recommend full re-review
        stales = omni_sweep.stale_markings([p2_finding("f1", "x", "a.py", 1)])
        self.assertEqual(stales["f1"]["status"], "stale")


class TestProviders(unittest.TestCase):
    def test_direct_provider_api_is_a_stub(self):
        provider = omni_sweep.DirectProviderAPI()
        with self.assertRaises(NotImplementedError):
            provider.call("prompt")

    def test_claude_spawn_provider_builds_command(self):
        provider = omni_sweep.ClaudeSpawnProvider()
        cmd = provider.build_command("PROMPT")
        self.assertEqual(cmd[0], "claude")
        self.assertIn("-p", cmd)
        self.assertIn("PROMPT", cmd)

    def test_claude_spawn_parses_json_verdicts(self):
        provider = omni_sweep.ClaudeSpawnProvider()
        parsed = provider.parse_verdicts(
            'noise\n[{"finding_id": "f1", "verdict": "fixed", '
            '"evidence_hunk_id": "a.py:H1"}]')
        self.assertEqual(parsed[0]["finding_id"], "f1")


class TestSweepEndToEndOffline(unittest.TestCase):
    def test_full_offline_sweep_over_git_fixture(self, tmp_path=None):
        import tempfile
        import pathlib
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            path, git = p2_repo(tmp)
            p2_write(path, "src/app.py", "def run():\n    return 1\n\ndef other():\n    return 2\n")
            p2_commit(git, "base")
            reviewed_head = subprocess.run(
                ["git", "-C", path, "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip()
            # push: fix the finding in a DIFFERENT file (cross-file), plus docs work
            p2_write(path, "src/runners.py", "def run():\n    return guarded()\n\ndef guarded():\n    return 1\n")
            p2_write(path, "docs/notes.md", "# Notes\nnew\n")
            p2_commit(git, "fix + docs")
            head = subprocess.run(
                ["git", "-C", path, "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip()

            findings = [p2_finding("f1", "run() is unguarded", "src/app.py", 2)]

            class FakeProvider:
                def call(self, prompt, session_id=None):
                    return json.dumps([{
                        "finding_id": "f1", "verdict": "fixed",
                        "evidence_hunk_id": "src/runners.py:H1",
                        "one_line": "moved into guarded()",
                        "confidence": 91}])

            result = omni_sweep.run_sweep(
                repo_root=path, reviewed_head=reviewed_head, head=head,
                findings=findings, provider=FakeProvider(), sweep_number=1)

            self.assertEqual(result["skip"], None)
            self.assertEqual(result["verdicts"][0]["verdict"], "fixed")
            self.assertIn("src/runners.py", result["relevant_files"])
            self.assertIn("docs/notes.md", result["residual"]["files"])
            self.assertIn("**Push check complete — head", result["breadcrumb"])
            self.assertIn("| finding | severity | kind | verdict | evidence |",
                          result["report"])

    def test_dry_run_makes_zero_calls_and_marks_judgment(self):
        import tempfile
        import pathlib
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            path, git = p2_repo(tmp)
            p2_write(path, "a.py", "keep = 0\nx = 1\n")
            p2_commit(git, "base")
            reviewed = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                                      capture_output=True, text=True).stdout.strip()
            p2_write(path, "a.py", "keep = 0\nx = 2\n")
            p2_commit(git, "change")
            head = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()

            class ExplodingProvider:
                def call(self, prompt, session_id=None):
                    raise AssertionError("dry run must not call the provider")

            result = omni_sweep.run_sweep(
                repo_root=path, reviewed_head=reviewed, head=head,
                findings=[p2_finding("f1", "x should be 2", "a.py", 1)],
                provider=ExplodingProvider(), sweep_number=1, dry_run=True)
            self.assertEqual(result["verdicts"][0]["verdict"], "needs_judgment")
            self.assertIn("dry run", result["verdicts"][0]["reason"])
            self.assertTrue(result["report"])


def _prompt_packets(prompt):
    """The packets JSON payload out of one build_batch_prompt string."""
    return json.loads(prompt.split("Findings:\n", 1)[1])


class ChunkProvider:
    """Chunking test double: records every model call (prompt + the
    session_id kwarg run_sweep threads through); replies come from
    `replies` (one per call) or echo the prompt's finding ids; call
    indexes in `fail_on` raise — the per-chunk degrade path."""

    def __init__(self, replies=None, fail_on=()):
        self.calls = []
        self.replies = list(replies or [])
        self.fail_on = set(fail_on)

    def call(self, prompt, session_id=None):
        index = len(self.calls)
        self.calls.append({"prompt": prompt, "session_id": session_id})
        if index in self.fail_on:
            raise RuntimeError("spawn boom %d" % index)
        if index < len(self.replies):
            return self.replies[index]
        ids = [p["finding_id"] for p in _prompt_packets(prompt)]
        return json.dumps([
            {"finding_id": i, "verdict": "not_fixed",
             "evidence_hunk_id": None, "confidence": 80,
             "one_line": "call-%d" % index} for i in ids])


@contextlib.contextmanager
def _chunk_repo(n_open):
    """A push delta with a small a.py fix, a big.py carrying 8 oversized
    hunks, and a deleted gone.py. Findings: f1..fN open (f1 anchored at
    big.py — its packet alone exceeds MAX_PACKET_CHARS; the rest anchored
    at a.py) plus one obsolete-anchored finding on the deleted file."""
    import pathlib
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        path, git = p2_repo(pathlib.Path(td), "chunky")
        base_big = ["line %03d" % i for i in range(1, 241)]
        p2_write(path, "a.py", "x = 1\n")
        p2_write(path, "big.py", "\n".join(base_big) + "\n")
        p2_write(path, "gone.py", "g = 1\n")
        p2_commit(git, "base")
        reviewed = subprocess.run(
            ["git", "-C", path, "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()
        push_big = list(base_big)
        for start in (10, 40, 70, 100, 130, 160, 190, 220):
            push_big[start - 1:start] = ["# fat %03d %s" % (start, "z" * 84)
                                         for _ in range(45)]
        p2_write(path, "big.py", "\n".join(push_big) + "\n")
        p2_write(path, "a.py", "x = 2\n")
        os.remove(os.path.join(path, "gone.py"))
        p2_commit(git, "push")
        head = subprocess.run(
            ["git", "-C", path, "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()
        findings = [p2_finding("f1", "the oversized concern", "big.py", 5)]
        findings += [p2_finding("f%d" % i, "small concern", "a.py", 1)
                     for i in range(2, n_open + 1)]
        findings.append(p2_finding("gone1", "deleted locus", "gone.py", 1))
        yield path, reviewed, head, findings


def _reload_with_env(over):
    """A fresh omni_sweep module loaded under a patched environment — the
    OMNIFORGE_SWEEP_CHUNK_THRESHOLD knob is read at import time."""
    saved = {k: os.environ.get(k) for k in over}
    for k, v in over.items():
        os.environ[k] = v
    try:
        return _load("omni_sweep_env_reload")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestChunkThresholdAndSplit(unittest.TestCase):
    """SC-1/D1/D2: batches over CHUNK_THRESHOLD (15, env-overridable) split
    ONCE into contiguous halves (first ceil(n/2), rest); each chunk gets its
    own evidence budget, prompt, and model call."""

    def test_threshold_boundary_15_one_call_16_two(self):
        with _chunk_repo(15) as (path, reviewed, head, findings):
            provider = ChunkProvider()
            omni_sweep.run_sweep(path, reviewed, head, findings, provider)
            self.assertEqual(len(provider.calls), 1)   # 15 open: at threshold
        with _chunk_repo(16) as (path, reviewed, head, findings):
            provider = ChunkProvider()
            omni_sweep.run_sweep(path, reviewed, head, findings, provider)
            self.assertEqual(len(provider.calls), 2)   # 16 open: chunked
            sizes = [len(_prompt_packets(c["prompt"]))
                     for c in provider.calls]
            self.assertEqual(sizes, [8, 8])             # ceil(16/2) halves

    def test_odd_batch_ceils_and_chunk_sizes_exceed_no_cap(self):
        # 31 findings split 16/15 — chunks may themselves exceed the
        # threshold (split ONCE, never recursively)
        with _chunk_repo(31) as (path, reviewed, head, findings):
            provider = ChunkProvider()
            omni_sweep.run_sweep(path, reviewed, head, findings, provider)
            self.assertEqual(len(provider.calls), 2)
            sizes = [len(_prompt_packets(c["prompt"]))
                     for c in provider.calls]
            self.assertEqual(sizes, [16, 15])
            # contiguous: no finding in both chunks
            ids1 = {p["finding_id"] for p in _prompt_packets(
                provider.calls[0]["prompt"])}
            ids2 = {p["finding_id"] for p in _prompt_packets(
                provider.calls[1]["prompt"])}
            self.assertFalse(ids1 & ids2)
            self.assertEqual(len(ids1 | ids2), 31)

    def test_per_chunk_fresh_evidence_budget(self):
        # f1's oversized big.py packet alone exhausts a MAX_PACKET_CHARS
        # budget; a SHARED budget would starve every chunk-2 finding — a
        # fresh per-chunk budget is the fix being pinned here.
        with _chunk_repo(16) as (path, reviewed, head, findings):
            provider = ChunkProvider()
            omni_sweep.run_sweep(path, reviewed, head, findings, provider)
            self.assertEqual(len(provider.calls), 2)
            chunk1 = _prompt_packets(provider.calls[0]["prompt"])
            chunk2 = _prompt_packets(provider.calls[1]["prompt"])
            self.assertTrue(chunk1[0].get("evidence_truncated"))
            self.assertTrue(chunk1[1].get("no_evidence"))  # starved in-chunk
            for packet in chunk2:
                self.assertTrue(packet["candidate_hunks"],
                                packet["finding_id"])

    def test_env_chunk_threshold_override(self):
        mod = _reload_with_env({"OMNIFORGE_SWEEP_CHUNK_THRESHOLD": "4"})
        self.assertEqual(mod.CHUNK_THRESHOLD, 4)
        with _chunk_repo(5) as (path, reviewed, head, findings):
            provider = ChunkProvider()
            mod.run_sweep(path, reviewed, head, findings, provider)
            self.assertEqual(len(provider.calls), 2)
        self.assertEqual(_reload_with_env(
            {"OMNIFORGE_SWEEP_CHUNK_THRESHOLD": "0"}).CHUNK_THRESHOLD, 15)
        self.assertEqual(_reload_with_env(
            {"OMNIFORGE_SWEEP_CHUNK_THRESHOLD": "abc"}).CHUNK_THRESHOLD, 15)


class TestChunkMergeAndDegrade(unittest.TestCase):
    """D2: merge order (obsolete-loci synthesized verdicts, then chunk-1,
    then chunk-2), the per-chunk id-set DROP, the single citation-rule pass
    over the concatenated packets, and per-chunk model-failure degrade."""

    def test_merged_order_obsolete_then_chunk1_then_chunk2(self):
        with _chunk_repo(16) as (path, reviewed, head, findings):
            result = omni_sweep.run_sweep(path, reviewed, head, findings,
                                          ChunkProvider())
            self.assertEqual(
                [v["finding_id"] for v in result["verdicts"]],
                ["gone1"] + ["f%d" % i for i in range(1, 17)])
            self.assertEqual(result["verdicts"][0]["verdict"], "obsolete")

    def test_cross_chunk_verdict_is_dropped(self):
        # render_report's by_id map is last-wins: a chunk-2 verdict
        # hallucinating a chunk-1 finding_id would shadow the real verdict.
        with _chunk_repo(16) as (path, reviewed, head, findings):
            provider = ChunkProvider(replies=[
                json.dumps([{"finding_id": "f1", "verdict": "not_fixed",
                             "evidence_hunk_id": None, "confidence": 80,
                             "one_line": "real chunk-1 verdict"}]),
                json.dumps([{"finding_id": "f1", "verdict": "fixed",
                             "evidence_hunk_id": "a.py:H1",
                             "confidence": 99,
                             "one_line": "hallucinated shadow"},
                            {"finding_id": "f9", "verdict": "not_fixed",
                             "evidence_hunk_id": None, "confidence": 80,
                             "one_line": "real chunk-2 verdict"}]),
            ])
            result = omni_sweep.run_sweep(path, reviewed, head, findings,
                                          provider)
            f1 = [v for v in result["verdicts"] if v["finding_id"] == "f1"]
            self.assertEqual(len(f1), 1)
            self.assertEqual(f1[0]["one_line"], "real chunk-1 verdict")
            f9 = [v for v in result["verdicts"] if v["finding_id"] == "f9"]
            self.assertEqual(f9[0]["one_line"], "real chunk-2 verdict")

    def test_citation_rule_runs_once_over_merged_packets(self):
        with _chunk_repo(16) as (path, reviewed, head, findings):
            provider = ChunkProvider(replies=[
                # chunk 1: f2 cites its own a.py hunk — survives
                json.dumps([{"finding_id": "f2", "verdict": "fixed",
                             "evidence_hunk_id": "a.py:H1",
                             "confidence": 90, "one_line": "own hunk"}]),
                # chunk 2: f9 cites a hunk in ITS candidate_hunks (only the
                # merged packet list knows it); f10 cites a hunk in no
                # packet at all — downgrade
                json.dumps([{"finding_id": "f9", "verdict": "fixed",
                             "evidence_hunk_id": "a.py:H1",
                             "confidence": 90, "one_line": "own hunk"},
                            {"finding_id": "f10", "verdict": "fixed",
                             "evidence_hunk_id": "zz.py:H9",
                             "confidence": 90, "one_line": "phantom"}]),
            ])
            result = omni_sweep.run_sweep(path, reviewed, head, findings,
                                          provider)
            by_id = {v["finding_id"]: v for v in result["verdicts"]}
            self.assertEqual(by_id["f2"]["verdict"], "fixed")
            self.assertEqual(by_id["f9"]["verdict"], "fixed")
            self.assertEqual(by_id["f10"]["verdict"], "needs_judgment")
            self.assertIn("citation", by_id["f10"]["reason"])

    def test_chunk2_failure_degrades_chunk2_only(self):
        with _chunk_repo(16) as (path, reviewed, head, findings):
            provider = ChunkProvider(fail_on={1})
            result = omni_sweep.run_sweep(path, reviewed, head, findings,
                                          provider)
            self.assertTrue(result["model_degraded"])
            by_id = {v["finding_id"]: v for v in result["verdicts"]}
            self.assertEqual(by_id["f2"]["one_line"], "call-0")  # chunk 1 kept
            self.assertEqual(by_id["f9"]["verdict"], "needs_judgment")
            self.assertIn("model unavailable", by_id["f9"]["reason"])
            self.assertGreaterEqual(result["timings"]["model_s"], 0.0)

    def test_chunk1_failure_degrades_chunk1_only(self):
        with _chunk_repo(16) as (path, reviewed, head, findings):
            provider = ChunkProvider(fail_on={0})
            result = omni_sweep.run_sweep(path, reviewed, head, findings,
                                          provider)
            self.assertTrue(result["model_degraded"])
            by_id = {v["finding_id"]: v for v in result["verdicts"]}
            self.assertEqual(by_id["f2"]["verdict"], "needs_judgment")
            self.assertIn("model unavailable", by_id["f2"]["reason"])
            self.assertEqual(by_id["f9"]["one_line"], "call-1")  # chunk 2 kept


class TestSessionsKey(unittest.TestCase):
    """D11: result['sessions'] lists the session ids actually used, in call
    order — call 1 rides the forwarded OMNIFORGE_SESSION_ID env, each
    additional call mints a fresh uuid."""

    def test_single_call_sessions_env_unset_then_set(self):
        with _chunk_repo(5) as (path, reviewed, head, findings):
            env = {k: v for k, v in os.environ.items()
                   if k != "OMNIFORGE_SESSION_ID"}
            with mock.patch.dict(os.environ, env, clear=True):
                result = omni_sweep.run_sweep(path, reviewed, head, findings,
                                              ChunkProvider())
                self.assertEqual(result["sessions"], [])
            with mock.patch.dict(os.environ, {"OMNIFORGE_SESSION_ID": "u1"}):
                result = omni_sweep.run_sweep(path, reviewed, head, findings,
                                              ChunkProvider())
                self.assertEqual(result["sessions"], ["u1"])

    def test_chunked_sessions_two_ids_first_is_env(self):
        with _chunk_repo(16) as (path, reviewed, head, findings):
            with mock.patch.dict(os.environ,
                                 {"OMNIFORGE_SESSION_ID": "env-sid"}):
                provider = ChunkProvider()
                result = omni_sweep.run_sweep(path, reviewed, head, findings,
                                              provider)
                self.assertEqual(len(result["sessions"]), 2)
                self.assertEqual(result["sessions"][0], "env-sid")
                self.assertNotEqual(result["sessions"][1], "env-sid")
                # call 1 rides the env (no explicit id); call 2 carries the
                # minted one — and it must differ from the env id
                self.assertIsNone(provider.calls[0]["session_id"])
                self.assertEqual(provider.calls[1]["session_id"],
                                 result["sessions"][1])


class TestProviderSessionAndTimeout(unittest.TestCase):
    """D11 wiring: the spawn timeout sits 60s above the forwarded engine
    ceiling (fallback 660 = engine default 600 + 60; replaces the old
    hardcoded 300), and build_command appends --session-id only when one
    is known (explicit arg over the forwarded env)."""

    def test_timeout_from_env_plus_grace(self):
        with mock.patch.dict(os.environ,
                             {"OMNIFORGE_SWEEP_PLUGIN_TIMEOUT_SECS": "90"}):
            self.assertEqual(omni_sweep.ClaudeSpawnProvider().timeout, 150)
        env = {k: v for k, v in os.environ.items()
               if k != "OMNIFORGE_SWEEP_PLUGIN_TIMEOUT_SECS"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(omni_sweep.ClaudeSpawnProvider().timeout, 720)
        with mock.patch.dict(
                os.environ,
                {"OMNIFORGE_SWEEP_PLUGIN_TIMEOUT_SECS": "garbage"}):
            self.assertEqual(omni_sweep.ClaudeSpawnProvider().timeout, 720)

    def test_build_command_session_id_flag(self):
        provider = omni_sweep.ClaudeSpawnProvider()
        env = {k: v for k, v in os.environ.items()
               if k != "OMNIFORGE_SESSION_ID"}
        with mock.patch.dict(os.environ, env, clear=True):
            cmd = provider.build_command("P")
            self.assertEqual(cmd,
                             ["claude", "-p", "P", "--output-format", "text"])
            self.assertNotIn("--session-id", cmd)
            self.assertEqual(provider.build_command("P", session_id="s2")[-2:],
                             ["--session-id", "s2"])
        with mock.patch.dict(os.environ, {"OMNIFORGE_SESSION_ID": "u"}):
            self.assertEqual(provider.build_command("P")[-2:],
                             ["--session-id", "u"])
            self.assertEqual(provider.build_command("P", session_id="s2")[-2:],
                             ["--session-id", "s2"])


if __name__ == "__main__":
    unittest.main()
