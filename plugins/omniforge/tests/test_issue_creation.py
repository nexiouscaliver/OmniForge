"""Tests for create_linked_issue MCP tool."""

import asyncio
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


class TestCreateLinkedIssue:
    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success(self, mock_run, tmp_path):
        from omnireview_mcp_server import _create_linked_issue
        repo = _make_repo(tmp_path)

        mock_run.return_value = _make_result(
            0,
            stdout="Creating issue in regenai-gitlab/cleo\nhttps://gitlab.com/regenai-gitlab/cleo/-/issues/42\n",
        )

        result = asyncio.run(_create_linked_issue(
            "136",
            "[MR !136] Missing null check",
            "The auth handler does not check for null user.",
            "omnireview,bug",
            repo,
        ))
        assert result["success"] is True
        assert result["action"] == "issue_created"
        assert "gitlab.com" in result["issue_url"]
        assert result["mr_id"] == "136"

        # Verify glab args
        call_args = mock_run.call_args[0][0]
        assert call_args[:3] == ["glab", "issue", "create"]
        assert "--linked-mr" in call_args
        assert "136" in call_args
        assert "--label" in call_args
        assert "omnireview,bug" in call_args

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_no_labels(self, mock_run, tmp_path):
        from omnireview_mcp_server import _create_linked_issue
        repo = _make_repo(tmp_path)

        mock_run.return_value = _make_result(
            0, stdout="https://gitlab.com/group/project/-/issues/1\n"
        )

        result = asyncio.run(_create_linked_issue(
            "136", "Title", "Description", "", repo
        ))
        assert result["success"] is True

        # Verify --label flag is NOT present when labels is empty
        call_args = mock_run.call_args[0][0]
        assert "--label" not in call_args

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_glab_failure(self, mock_run, tmp_path):
        from omnireview_mcp_server import _create_linked_issue
        repo = _make_repo(tmp_path)

        mock_run.return_value = _make_result(1, stderr="403 Forbidden")

        result = asyncio.run(_create_linked_issue(
            "136", "Title", "Description", "", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "issue_creation_failed"
        assert "403" in result["error"]

    def test_empty_title(self, tmp_path):
        from omnireview_mcp_server import _create_linked_issue
        repo = _make_repo(tmp_path)

        result = asyncio.run(_create_linked_issue(
            "136", "", "Description", "", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_mr_id(self, tmp_path):
        from omnireview_mcp_server import _create_linked_issue
        repo = _make_repo(tmp_path)

        result = asyncio.run(_create_linked_issue(
            "abc", "Title", "Desc", "", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"
