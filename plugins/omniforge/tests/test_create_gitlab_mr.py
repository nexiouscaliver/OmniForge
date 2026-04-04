"""Tests for create_gitlab_mr MCP tool."""

import asyncio
import os
import sys
from unittest.mock import patch

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


class TestCreateGitlabMrDefaults:
    """Test default argument construction."""

    def test_defaults_include_fill_and_push(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        with patch("omniforge_mcp_server.run_exec") as mock_exec:
            mock_exec.return_value = _make_result(
                stdout="https://gitlab.com/group/repo/-/merge_requests/1"
            )
            result = asyncio.get_event_loop().run_until_complete(
                _create_gitlab_mr(repo_root=repo)
            )
        assert result["success"] is True
        args = mock_exec.call_args[0][0]
        assert "glab" in args
        assert "--fill" in args
        assert "--fill-commit-body" in args
        assert "--push" in args
        assert "--target-branch" in args
        assert "main" in args

    def test_mr_url_parsed_from_output(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        with patch("omniforge_mcp_server.run_exec") as mock_exec:
            mock_exec.return_value = _make_result(
                stdout="Creating MR...\nhttps://gitlab.com/g/r/-/merge_requests/42\nDone"
            )
            result = asyncio.get_event_loop().run_until_complete(
                _create_gitlab_mr(repo_root=repo)
            )
        assert result["mr_url"] == "https://gitlab.com/g/r/-/merge_requests/42"


class TestCreateGitlabMrCustomTitleDescription:
    """Test custom title/description with fill=false."""

    def test_fill_false_omits_fill_flags(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        with patch("omniforge_mcp_server.run_exec") as mock_exec:
            mock_exec.return_value = _make_result(
                stdout="https://gitlab.com/g/r/-/merge_requests/5"
            )
            result = asyncio.get_event_loop().run_until_complete(
                _create_gitlab_mr(
                    repo_root=repo,
                    title="Fix login bug",
                    description="Fixes the auth timeout",
                    fill=False,
                    fill_commit_body=True,
                )
            )
        assert result["success"] is True
        args = mock_exec.call_args[0][0]
        assert "--fill" not in args
        # fill_commit_body should NOT be passed when fill=False
        assert "--fill-commit-body" not in args
        assert "--title" in args
        assert "Fix login bug" in args
        assert "--description" in args


class TestCreateGitlabMrLabelsAndAssignees:
    """Test labels, assignees, and reviewers."""

    def test_labels_and_assignees_in_args(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        with patch("omniforge_mcp_server.run_exec") as mock_exec:
            mock_exec.return_value = _make_result(
                stdout="https://gitlab.com/g/r/-/merge_requests/10"
            )
            result = asyncio.get_event_loop().run_until_complete(
                _create_gitlab_mr(
                    repo_root=repo,
                    labels="bug,needs-review",
                    assignees="alice",
                    reviewers="bob",
                )
            )
        assert result["success"] is True
        args = mock_exec.call_args[0][0]
        assert "--label" in args
        assert "bug,needs-review" in args
        assert "--assignee" in args
        assert "alice" in args
        assert "--reviewer" in args
        assert "bob" in args

    def test_gitlab_style_labels_accepted(self, tmp_path):
        """Labels like type::bug and group/backend should be allowed."""
        from omniforge_mcp_server import validate_labels
        result = validate_labels("type::bug,group/backend,P1")
        assert result == "type::bug,group/backend,P1"

    def test_labels_with_control_chars_rejected(self, tmp_path):
        from omniforge_mcp_server import validate_labels
        with pytest.raises(ValueError, match="control characters"):
            validate_labels("bug\x00injected")


class TestCreateGitlabMrDraftAndFlags:
    """Test boolean flags like draft, remove_source_branch, squash."""

    def test_draft_flag(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        with patch("omniforge_mcp_server.run_exec") as mock_exec:
            mock_exec.return_value = _make_result(
                stdout="https://gitlab.com/g/r/-/merge_requests/7"
            )
            result = asyncio.get_event_loop().run_until_complete(
                _create_gitlab_mr(repo_root=repo, draft=True)
            )
        args = mock_exec.call_args[0][0]
        assert "--draft" in args

    def test_web_flag_excludes_yes(self, tmp_path):
        """--web and --yes conflict in glab, so --yes must be omitted when --web is used."""
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        with patch("omniforge_mcp_server.run_exec") as mock_exec:
            mock_exec.return_value = _make_result(
                stdout="https://gitlab.com/g/r/-/merge_requests/9"
            )
            result = asyncio.get_event_loop().run_until_complete(
                _create_gitlab_mr(repo_root=repo, web=True)
            )
        args = mock_exec.call_args[0][0]
        assert "--web" in args
        assert "--yes" not in args

    def test_yes_flag_present_without_web(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        with patch("omniforge_mcp_server.run_exec") as mock_exec:
            mock_exec.return_value = _make_result(
                stdout="https://gitlab.com/g/r/-/merge_requests/10"
            )
            result = asyncio.get_event_loop().run_until_complete(
                _create_gitlab_mr(repo_root=repo)
            )
        args = mock_exec.call_args[0][0]
        assert "--yes" in args
        assert "--web" not in args

    def test_source_branch_and_target_branch(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        with patch("omniforge_mcp_server.run_exec") as mock_exec:
            mock_exec.return_value = _make_result(
                stdout="https://gitlab.com/g/r/-/merge_requests/8"
            )
            result = asyncio.get_event_loop().run_until_complete(
                _create_gitlab_mr(
                    repo_root=repo,
                    source_branch="feature-x",
                    target_branch="develop",
                )
            )
        args = mock_exec.call_args[0][0]
        assert "--source-branch" in args
        assert "feature-x" in args
        assert "--target-branch" in args
        assert "develop" in args


class TestCreateGitlabMrErrorHandling:
    """Test non-zero return code and validation errors."""

    def test_nonzero_returncode(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        with patch("omniforge_mcp_server.run_exec") as mock_exec:
            mock_exec.return_value = _make_result(
                returncode=1,
                stderr="authentication required",
            )
            result = asyncio.get_event_loop().run_until_complete(
                _create_gitlab_mr(repo_root=repo)
            )
        assert result["success"] is False
        assert "authentication required" in result["error"]
        assert result["error_type"] == "mr_creation_failed"

    def test_invalid_repo_root(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        result = asyncio.get_event_loop().run_until_complete(
            _create_gitlab_mr(repo_root="/nonexistent/path")
        )
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_source_branch_rejected(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        result = asyncio.get_event_loop().run_until_complete(
            _create_gitlab_mr(repo_root=repo, source_branch="branch;rm -rf /")
        )
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_target_branch_rejected(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        result = asyncio.get_event_loop().run_until_complete(
            _create_gitlab_mr(repo_root=repo, target_branch="branch|evil")
        )
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_title_rejected(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        result = asyncio.get_event_loop().run_until_complete(
            _create_gitlab_mr(
                repo_root=repo,
                title="bad;title",
                fill=False,
            )
        )
        assert result["success"] is False
        assert result["error_type"] == "validation_error"


class TestCreateGitlabMrRelatedIssue:
    """Test issue linking and copy-issue-labels."""

    def test_related_issue_and_copy_labels(self, tmp_path):
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        with patch("omniforge_mcp_server.run_exec") as mock_exec:
            mock_exec.return_value = _make_result(
                stdout="https://gitlab.com/g/r/-/merge_requests/20"
            )
            result = asyncio.get_event_loop().run_until_complete(
                _create_gitlab_mr(
                    repo_root=repo,
                    related_issue="42",
                    copy_issue_labels=True,
                )
            )
        args = mock_exec.call_args[0][0]
        assert "--related-issue" in args
        assert "42" in args
        assert "--copy-issue-labels" in args

    def test_copy_issue_labels_without_issue_ignored(self, tmp_path):
        """copy_issue_labels should not appear without related_issue."""
        from omniforge_mcp_server import _create_gitlab_mr
        repo = _make_repo(tmp_path)
        with patch("omniforge_mcp_server.run_exec") as mock_exec:
            mock_exec.return_value = _make_result(
                stdout="https://gitlab.com/g/r/-/merge_requests/21"
            )
            result = asyncio.get_event_loop().run_until_complete(
                _create_gitlab_mr(
                    repo_root=repo,
                    copy_issue_labels=True,
                )
            )
        args = mock_exec.call_args[0][0]
        assert "--copy-issue-labels" not in args
