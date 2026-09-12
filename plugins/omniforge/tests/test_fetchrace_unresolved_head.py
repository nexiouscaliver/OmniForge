"""Fetch-race tests (FR-2/FR-3, goal engine-sweep-fetch-race): a sha that
does not resolve in --repo-root is a LOUD failure — exit 3, one stderr
REASON line carrying the literal UNRESOLVED_HEAD, and NO stdout result JSON
— never a bogus SKIP_EMPTY_DELTA/SKIP_TREE_HASH_EQUAL. Legal skips stay
legal: resolvable merge-only deltas still skip SKIP_TREE_HASH_EQUAL;
resolvable real deltas still produce verdicts. Drives omni_sweep.main(argv)
directly over real-git fixtures (p2_repo-style)."""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                       "skills", "omnicheck-gitlab", "scripts"))

_spec = importlib.util.spec_from_file_location(
    "omni_sweep_fetchrace", os.path.join(SCRIPTS, "omni_sweep.py"))
omni_sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(omni_sweep)

DEAD40 = "e" * 40   # a well-formed sha that resolves nowhere


def _repo(root, name="repo"):
    path = os.path.join(root, name)
    os.makedirs(path)

    def git(*args):
        return subprocess.run(["git", "-C", path, *args], check=True,
                              capture_output=True, text=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "fetchrace@example.com")
    git("config", "user.name", "fetchrace")
    return path, git


def _write(path, rel, content):
    full = os.path.join(path, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)


def _commit(git, msg):
    git("add", "-A")
    git("commit", "-q", "--allow-empty", "-m", msg)


def _head(path):
    return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                          check=True, capture_output=True,
                          text=True).stdout.strip()


def _findings(root):
    path = os.path.join(root, "findings.json")
    with open(path, "w") as fh:
        json.dump([{"id": "f1", "disposition": "not_fixed",
                    "severity": "important", "kind": "code",
                    "file_path": "a.py", "line_number": 1,
                    "body": "x should be guarded", "reason": ""}], fh)
    return path


def _main(repo, reviewed, head, findings):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = omni_sweep.main(["--repo-root", repo, "--reviewed-head",
                              reviewed, "--head", head, "--findings",
                              findings, "--model", "none"])
    return rc, out.getvalue(), err.getvalue()


class FetchRaceWitness(unittest.TestCase):

    def test_fetchrace_plugin_witness_missing_head_skips_empty_delta_today(self):
        # DEFECT RECEIPT (RED phase): rev-parse of a missing sha yields
        # EMPTY stdout, the diff fails to empty text, so the plugin answers
        # rc 0 skip=SKIP_EMPTY_DELTA — the exact production mechanism.
        # Rewritten in the GREEN commit to expect exit 3.
        with tempfile.TemporaryDirectory() as td:
            repo, git = _repo(td)
            _write(repo, "a.py", "x = 1\n")
            _commit(git, "base")
            reviewed = _head(repo)
            rc, out, err = _main(repo, reviewed, DEAD40, _findings(td))
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["skip"], "SKIP_EMPTY_DELTA")


class FetchRaceUnresolvedHead(unittest.TestCase):

    def test_fetchrace_missing_head_exits_3_with_unresolved_head_reason(self):
        with tempfile.TemporaryDirectory() as td:
            repo, git = _repo(td)
            _write(repo, "a.py", "x = 1\n")
            _commit(git, "base")
            reviewed = _head(repo)
            rc, out, err = _main(repo, reviewed, DEAD40, _findings(td))
            self.assertEqual(rc, 3)
            self.assertIn("UNRESOLVED_HEAD", err)
            self.assertIn(DEAD40[:8], err)
            self.assertIn(repo, err)

    def test_fetchrace_missing_reviewed_head_exits_3_with_unresolved_head_reason(self):
        with tempfile.TemporaryDirectory() as td:
            repo, git = _repo(td)
            _write(repo, "a.py", "x = 1\n")
            _commit(git, "base")
            head = _head(repo)
            rc, out, err = _main(repo, DEAD40, head, _findings(td))
            self.assertEqual(rc, 3)
            self.assertIn("UNRESOLVED_HEAD", err)
            self.assertIn(DEAD40[:8], err)

    def test_fetchrace_unresolvable_prints_no_skip_result_json(self):
        with tempfile.TemporaryDirectory() as td:
            repo, git = _repo(td)
            _write(repo, "a.py", "x = 1\n")
            _commit(git, "base")
            reviewed = _head(repo)
            rc, out, err = _main(repo, reviewed, DEAD40, _findings(td))
            self.assertEqual(rc, 3)
            self.assertEqual(out.strip(), "")      # nothing carrying "skip"
            self.assertNotIn('"skip"', out)


class FetchRaceLegalSkips(unittest.TestCase):

    def test_fetchrace_merge_only_delta_still_skips_tree_hash_equal(self):
        with tempfile.TemporaryDirectory() as td:
            repo, git = _repo(td)
            _write(repo, "a.py", "x = 1\n")
            _commit(git, "base")
            reviewed = _head(repo)
            _commit(git, "merge-only: empty commit, same tree")
            head = _head(repo)
            rc, out, err = _main(repo, reviewed, head, _findings(td))
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["skip"], "SKIP_TREE_HASH_EQUAL")

    def test_fetchrace_real_delta_produces_verdicts(self):
        # The finding's locus (a.py:1, "x = 1") stays untouched by the
        # delta, so it re-anchors open instead of being marked obsolete —
        # --model none then answers needs_judgment for it.
        with tempfile.TemporaryDirectory() as td:
            repo, git = _repo(td)
            _write(repo, "a.py", "x = 1\ny = 2\n")
            _commit(git, "base")
            reviewed = _head(repo)
            _write(repo, "a.py", "x = 1\ny = 3\n")
            _commit(git, "change")
            head = _head(repo)
            rc, out, err = _main(repo, reviewed, head, _findings(td))
            self.assertEqual(rc, 0)
            parsed = json.loads(out)
            self.assertIsNone(parsed["skip"])
            self.assertTrue(parsed["verdicts"])   # --model none -> judgment
            self.assertEqual(parsed["verdicts"][0]["verdict"],
                             "needs_judgment")


if __name__ == "__main__":
    unittest.main()
