"""Tests for posting tools: summary, inline threads, and full review."""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))


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


SAMPLE_DIFF_REFS_JSON = json.dumps({
    "iid": 136,
    "diff_refs": {
        "base_sha": "aaa111",
        "head_sha": "bbb222",
        "start_sha": "ccc333",
    },
})


# ── _post_review_summary Tests ────────────────────────────


class TestPostReviewSummary:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success(self, mock_run, tmp_path):
        from omniforge_mcp_server import _post_review_summary
        repo = _make_repo(tmp_path)

        mock_run.return_value = _make_result(0)

        result = asyncio.run(_post_review_summary("136", "## Review Summary\nLooks good.", repo))
        assert result["success"] is True
        assert result["action"] == "summary_posted"

        # Verify glab mr note was called with the summary
        call_args = mock_run.call_args[0][0]
        assert call_args[:3] == ["glab", "mr", "note"]
        assert "136" in call_args
        assert "## Review Summary\nLooks good." in call_args

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_glab_failure(self, mock_run, tmp_path):
        from omniforge_mcp_server import _post_review_summary
        repo = _make_repo(tmp_path)

        mock_run.return_value = _make_result(1, stderr="403 Forbidden")

        result = asyncio.run(_post_review_summary("136", "Summary text", repo))
        assert result["success"] is False
        assert result["error_type"] == "post_failed"

    def test_empty_summary(self, tmp_path):
        from omniforge_mcp_server import _post_review_summary
        repo = _make_repo(tmp_path)

        result = asyncio.run(_post_review_summary("136", "", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_mr_id(self, tmp_path):
        from omniforge_mcp_server import _post_review_summary
        repo = _make_repo(tmp_path)

        result = asyncio.run(_post_review_summary("abc", "Summary", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"


# ── _post_inline_thread Tests ─────────────────────────────


class TestPostInlineThread:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success(self, mock_run, tmp_path):
        from omniforge_mcp_server import _post_inline_thread
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # glab mr view (diff refs)
                return _make_result(0, SAMPLE_DIFF_REFS_JSON)
            return _make_result(0)  # glab api POST

        mock_run.side_effect = side_effect

        result = asyncio.run(_post_inline_thread(
            "136", "src/app.py", 42, "**Important** — Missing null check", repo
        ))
        assert result["success"] is True
        assert result["file"] == "src/app.py"
        assert result["line"] == 42

        # Verify the API call included position data
        api_call = mock_run.call_args_list[1][0][0]
        assert "projects/:fullpath/merge_requests/136/discussions" in api_call[2]
        assert "--raw-field" in api_call
        # Check SHAs were included
        raw_fields = [a for a in api_call if a.startswith("position[")]
        assert any("base_sha" in f for f in raw_fields)
        assert any("head_sha" in f for f in raw_fields)
        assert any("new_path]=src/app.py" in f for f in raw_fields)
        assert any("new_line]=42" in f for f in raw_fields)

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_mr_not_found(self, mock_run, tmp_path):
        from omniforge_mcp_server import _post_inline_thread
        repo = _make_repo(tmp_path)

        mock_run.return_value = _make_result(1, stderr="not found")

        result = asyncio.run(_post_inline_thread(
            "999", "file.py", 10, "Finding text", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "mr_not_found"

    def test_empty_body(self, tmp_path):
        from omniforge_mcp_server import _post_inline_thread
        repo = _make_repo(tmp_path)

        result = asyncio.run(_post_inline_thread("136", "file.py", 10, "", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_line_number(self, tmp_path):
        from omniforge_mcp_server import _post_inline_thread
        repo = _make_repo(tmp_path)

        result = asyncio.run(_post_inline_thread("136", "file.py", 0, "Body", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_api_post_failure(self, mock_run, tmp_path):
        from omniforge_mcp_server import _post_inline_thread
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_DIFF_REFS_JSON)
            return _make_result(1, stderr="400 Bad Request")

        mock_run.side_effect = side_effect

        result = asyncio.run(_post_inline_thread(
            "136", "file.py", 10, "Body text", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "post_failed"


# ── _post_full_review Tests ───────────────────────────────


class TestPostFullReview:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success(self, mock_run, tmp_path):
        from omniforge_mcp_server import _post_full_review
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            cmd = " ".join(args)
            if "mr" in cmd and "view" in cmd and "-F" in cmd:
                return _make_result(0, SAMPLE_DIFF_REFS_JSON)
            return _make_result(0)

        mock_run.side_effect = side_effect

        findings = [
            {"file_path": "a.py", "line_number": 10, "body": "Issue 1"},
            {"file_path": "b.py", "line_number": 20, "body": "Issue 2"},
        ]
        result = asyncio.run(_post_full_review("136", "Summary text", findings, repo))
        assert result["success"] is True
        assert result["summary_posted"] is True
        assert result["threads_posted"] == 2
        assert result["threads_total"] == 2

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_partial_failure(self, mock_run, tmp_path):
        from omniforge_mcp_server import _post_full_review
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            cmd = " ".join(args)
            if "mr" in cmd and "note" in cmd:
                return _make_result(0)  # summary OK
            if "mr" in cmd and "view" in cmd and "-F" in cmd:
                return _make_result(0, SAMPLE_DIFF_REFS_JSON)
            if "api" in cmd and call_count > 4:
                return _make_result(1, stderr="error")  # 2nd thread fails
            return _make_result(0)

        mock_run.side_effect = side_effect

        findings = [
            {"file_path": "a.py", "line_number": 10, "body": "Issue 1"},
            {"file_path": "b.py", "line_number": 20, "body": "Issue 2"},
        ]
        result = asyncio.run(_post_full_review("136", "Summary", findings, repo))
        assert result["success"] is False
        assert result["summary_posted"] is True
        assert result["threads_posted"] >= 1
        assert len(result["errors"]) >= 1

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_invalid_finding_skipped(self, mock_run, tmp_path):
        from omniforge_mcp_server import _post_full_review
        repo = _make_repo(tmp_path)

        mock_run.return_value = _make_result(0)  # summary post succeeds

        # Finding with missing fields
        findings = [
            {"file_path": "", "line_number": 0, "body": ""},
        ]
        result = asyncio.run(_post_full_review("136", "Summary", findings, repo))
        assert result["success"] is False
        assert any("missing file_path" in e for e in result["errors"])

    def test_empty_findings_list(self, tmp_path):
        from omniforge_mcp_server import _post_full_review
        repo = _make_repo(tmp_path)

        with patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = _make_result(0)
            result = asyncio.run(_post_full_review("136", "Summary", [], repo))

        assert result["success"] is True
        assert result["summary_posted"] is True
        assert result["threads_posted"] == 0
        assert result["threads_total"] == 0
