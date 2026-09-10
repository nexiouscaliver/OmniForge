"""P1 audit fixes in the MCP server + truncation guard (audit fixes 2/4/5).

- approve_mr guards: refuse on unresolved resolvable threads, missing
  omniforge::reviewed label, or head moved since the check (fix 2).
- fetch_mr_discussions thread payloads carry old_path/old_line + the anchor
  sha (fix 4).
- fetch_mr_data exposes truncated_files + head_sha, and omni_verdict applies
  the truncation guard: threads anchored in cut regions become unverified
  with a stated reason (fix 5).
"""

import asyncio
import importlib.util
import json
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                       "skills", "omnicheck-gitlab", "scripts"))


def _load_verdict():
    spec = importlib.util.spec_from_file_location(
        "omni_verdict_p1g", os.path.join(SCRIPTS, "omni_verdict.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


omni_verdict = _load_verdict()


def _make_repo(tmp_path):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    os.makedirs(os.path.join(repo, ".git"))
    return repo


def _make_result(returncode=0, stdout="", stderr=""):
    class R:
        pass
    r = R()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _mr_json(labels=("omniforge::reviewed",), head_sha="deadbeef"):
    return json.dumps({
        "iid": 136,
        "labels": list(labels),
        "diff_refs": {"base_sha": "aaa111", "head_sha": head_sha,
                      "start_sha": "ccc333"},
    })


def _discussions(unresolved=0, resolved=0):
    discs = []
    for i in range(unresolved):
        discs.append({"id": f"open-{i}", "resolvable": True, "resolved": False,
                      "notes": [{"body": f"finding {i}", "system": False,
                                 "author": {"username": "r"}}]})
    for i in range(resolved):
        discs.append({"id": f"done-{i}", "resolvable": True, "resolved": True,
                      "notes": [{"body": f"done {i}", "system": False,
                                 "author": {"username": "r"}}]})
    # general comment threads are resolvable=False: never approval blockers
    discs.append({"id": "general-1", "resolvable": False, "resolved": False,
                  "notes": [{"body": "nice work", "system": False,
                             "author": {"username": "r"}}]})
    return json.dumps(discs)


class TestApproveMrGuards:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_refuses_when_unresolved_resolvable_threads(self, mock_run, tmp_path):
        """Fix 2: never approve past an unresolved resolvable thread."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, _mr_json()),
            _make_result(0, _discussions(unresolved=2)),
        ]
        result = asyncio.run(_approve_mr("136", repo))
        assert result["success"] is False
        assert result["error_type"] == "unresolved_threads"
        assert mock_run.call_count == 2  # view + discussions, no approve POST
        approve_calls = [c for c in mock_run.call_args_list
                         if "approve" in c[0][0][2]]
        assert not approve_calls

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_refuses_when_reviewed_label_missing(self, mock_run, tmp_path):
        """Fix 2: the bot approval must never certify an unreviewed MR."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, _mr_json(labels=[])),
            _make_result(0, _discussions()),
        ]
        result = asyncio.run(_approve_mr("136", repo))
        assert result["success"] is False
        assert result["error_type"] == "missing_reviewed_label"
        assert "omniforge::reviewed" in result["error"]
        approve_calls = [c for c in mock_run.call_args_list
                         if "approve" in c[0][0][2]]
        assert not approve_calls

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_refuses_when_head_moved_since_check(self, mock_run, tmp_path):
        """Fix 2/7: an approval for a head the check never saw is refused."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, _mr_json(head_sha="deadbeef")),
            _make_result(0, _discussions()),
        ]
        result = asyncio.run(_approve_mr("136", repo, checked_sha="0badf00d"))
        assert result["success"] is False
        assert result["error_type"] == "head_moved"
        approve_calls = [c for c in mock_run.call_args_list
                         if "approve" in c[0][0][2]]
        assert not approve_calls

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_guard_check_failure_fails_closed(self, mock_run, tmp_path):
        """A discussions fetch the guard cannot run = refuse, not approve."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, _mr_json()),
            _make_result(1, stderr="500 Server Error"),
        ]
        result = asyncio.run(_approve_mr("136", repo))
        assert result["success"] is False
        assert result["error_type"] == "guard_check_failed"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_approves_when_guards_pass(self, mock_run, tmp_path):
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, _mr_json(head_sha="deadbeef")),
            _make_result(0, _discussions(resolved=3)),
            _make_result(0, stdout="{}"),
        ]
        result = asyncio.run(_approve_mr("136", repo, checked_sha="deadbeef"))
        assert result["success"] is True
        assert result["sha"] == "deadbeef"
        assert result["action"] == "mr_approved"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_general_comments_do_not_block(self, mock_run, tmp_path):
        """resolvable=False threads (general comments) are not blockers."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, _mr_json()),
            _make_result(0, _discussions(unresolved=0, resolved=0)),
            _make_result(0, stdout="{}"),
        ]
        result = asyncio.run(_approve_mr("136", repo))
        assert result["success"] is True


class TestFetchDiscussionsAnchors:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_thread_payload_carries_old_side_and_anchor_sha(self, mock_run, tmp_path):
        """Fix 4: old_path/old_line + anchor sha travel with each thread."""
        from omniforge_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)
        discs = json.dumps([
            {"id": "d1", "resolvable": True, "resolved": False,
             "notes": [
                 {"body": "**Important** — X", "system": False,
                  "author": {"username": "r"},
                  "type": "DiffNote",
                  "position": {
                      "new_path": "src/new.py", "new_line": 42,
                      "old_path": "src/old.py", "old_line": 39,
                      "head_sha": "deadbeef", "base_sha": "aaa111",
                      "position_type": "text",
                  }}]}])
        mock_run.side_effect = [
            _make_result(0, _mr_json()),
            _make_result(0, discs),
        ]
        result = asyncio.run(_fetch_mr_discussions("136", repo))
        assert result["success"] is True
        d = result["discussions"][0]
        assert d["file_path"] == "src/new.py"
        assert d["line_number"] == 42
        assert d["old_path"] == "src/old.py"
        assert d["old_line"] == 39
        assert d["anchor_sha"] == "deadbeef"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_general_thread_anchor_fields_absent_not_wrong(self, mock_run, tmp_path):
        from omniforge_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)
        discs = json.dumps([
            {"id": "d2", "resolvable": False, "resolved": None,
             "notes": [{"body": "question about the plan", "system": False,
                        "author": {"username": "r"}}]}])
        mock_run.side_effect = [
            _make_result(0, _mr_json()),
            _make_result(0, discs),
        ]
        result = asyncio.run(_fetch_mr_discussions("136", repo))
        d = result["discussions"][0]
        assert d["type"] == "general"
        assert d["old_path"] is None
        assert d["anchor_sha"] == ""


def _big_diff():
    """a.py lands fully inside the cap; b.py's hunks fall past the cut."""
    lines = ["diff --git a/small/a.py b/small/a.py",
             "index 111..222 100644",
             "--- a/small/a.py", "+++ b/small/a.py",
             "@@ -1,1 +1,2 @@", " ctx", "+added-in-a"]
    lines.append("diff --git a/big/b.py b/big/b.py")
    lines.append("index 333..444 100644")
    lines.append("--- a/big/b.py")
    lines.append("+++ b/big/b.py")
    lines.append("@@ -1,1 +1,2 @@")
    lines.append(" ctx")
    for i in range(10001):
        lines.append(f"+filler-{i}")
    return "\n".join(lines)


class TestFetchMrDataTruncation:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_truncated_files_exposed(self, mock_run, tmp_path):
        """Fix 5: files cut by the cap are named so threads there can be
        flagged needs_human instead of silently unseen."""
        from omniforge_mcp_server import _fetch_mr_data, MAX_DIFF_LINES
        repo = _make_repo(tmp_path)
        diff = _big_diff()

        def side_effect(args, cwd=None, timeout=60, env=None):
            if args[:3] == ["glab", "auth", "status"]:
                return _make_result(0, stdout="ok")
            if args[:3] == ["glab", "mr", "view"]:
                if "-F" in args and "json" in args:
                    return _make_result(0, _mr_json(head_sha="feedface"))
                return _make_result(0, stdout="")  # comments
            if args[:3] == ["glab", "mr", "diff"]:
                return _make_result(0, stdout=diff)
            return _make_result(0, stdout="")

        mock_run.side_effect = side_effect
        result = asyncio.run(_fetch_mr_data("136", repo))
        assert result["success"] is True
        assert result["diff_truncated"] is True
        assert result["truncated_files"] == ["big/b.py"]
        assert "small/a.py" not in result["truncated_files"]
        assert result["head_sha"] == "feedface"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_no_truncation_section_when_diff_small(self, mock_run, tmp_path):
        from omniforge_mcp_server import _fetch_mr_data
        repo = _make_repo(tmp_path)

        def side_effect(args, cwd=None, timeout=60, env=None):
            if args[:3] == ["glab", "auth", "status"]:
                return _make_result(0, stdout="ok")
            if args[:3] == ["glab", "mr", "view"]:
                if "-F" in args and "json" in args:
                    return _make_result(0, _mr_json())
                return _make_result(0, stdout="")
            if args[:3] == ["glab", "mr", "diff"]:
                return _make_result(0,
                    stdout="diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
                           "@@ -1,1 +1,2 @@\n ctx\n+one\n")
            return _make_result(0, stdout="")

        mock_run.side_effect = side_effect
        result = asyncio.run(_fetch_mr_data("136", repo))
        assert result["diff_truncated"] is False
        assert result["truncated_files"] == []


class TestTruncationGuardModel:
    def test_unverified_finding_in_cut_file_gets_stated_reason(self):
        findings = [
            {"id": "f1", "disposition": "not_fixed", "severity": "critical",
             "kind": "code", "file_path": "big/b.py", "reason": ""},
            {"id": "f2", "disposition": "not_fixed", "severity": "minor",
             "kind": "docs", "file_path": "small/a.py", "reason": ""},
        ]
        out = omni_verdict.apply_truncation_guard(findings, ["big/b.py"])
        assert "truncat" in out[0]["reason"].lower()
        assert out[0]["disposition"] == "not_fixed"  # stays open, fail-closed
        assert out[1]["reason"] == ""

    def test_verified_finding_not_retroflagged(self):
        findings = [
            {"id": "f1", "disposition": "fixed", "severity": "critical",
             "kind": "code", "file_path": "big/b.py",
             "reason": "verification: SILENTLY_APPLIED"},
        ]
        out = omni_verdict.apply_truncation_guard(findings, ["big/b.py"])
        assert out[0]["disposition"] == "fixed"
        assert "verification" in out[0]["reason"]


if __name__ == "__main__":
    import unittest
    unittest.main()
