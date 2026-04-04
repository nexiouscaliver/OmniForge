"""Tests for worktree creation and cleanup in OmniForge MCP server."""

import asyncio
import os
import shutil
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from omniforge_mcp_server import (  # noqa: E402
    _cleanup_review_worktrees,
    _create_review_worktrees,
)


# ── Helpers ──────────────────────────────────────────────


def _make_result(returncode, stdout="", stderr=""):
    """Create a mock result matching run_exec's return shape."""
    class Result:
        pass
    r = Result()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _make_repo(tmp_path):
    """Create a fake git repo directory and return its path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return str(repo)


# ── _create_review_worktrees Tests ───────────────────────


class TestCreateReviewWorktrees:
    """Tests for _create_review_worktrees."""

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_creates_three_worktrees(self, mock_run, tmp_path):
        """All calls succeed -- should return 3 worktree paths."""
        repo = _make_repo(tmp_path)
        worktrees_dir = os.path.join(repo, ".worktrees")

        def side_effect(args, cwd=None, timeout=60):
            # check-ignore: .worktrees already ignored
            if args[:2] == ["git", "check-ignore"]:
                return _make_result(0)
            # worktree prune
            if args[:2] == ["git", "worktree"] and "prune" in args:
                return _make_result(0)
            # fetch
            if args[:2] == ["git", "fetch"]:
                return _make_result(0)
            # worktree add -- actually create the directory
            if args[:2] == ["git", "worktree"] and "add" in args:
                path = args[3]  # ["git", "worktree", "add", <path>, ...]
                os.makedirs(path, exist_ok=True)
                return _make_result(0)
            # worktree remove (stale cleanup)
            if args[:2] == ["git", "worktree"] and "remove" in args:
                return _make_result(0)
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(
            _create_review_worktrees("42", "feature/test", repo)
        )

        assert result["success"] is True
        assert len(result["worktrees"]) == 3
        assert "analyst" in result["worktrees"]
        assert "codebase" in result["worktrees"]
        assert "security" in result["worktrees"]
        # All paths must be absolute
        for wt_type, path in result["worktrees"].items():
            assert os.path.isabs(path), f"{wt_type} path not absolute: {path}"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_fetch_failure_aborts(self, mock_run, tmp_path):
        """Fetch fails -- should abort with fetch_failed error."""
        repo = _make_repo(tmp_path)

        def side_effect(args, cwd=None, timeout=60):
            if args[:2] == ["git", "check-ignore"]:
                return _make_result(0)
            if args[:2] == ["git", "worktree"] and "prune" in args:
                return _make_result(0)
            if args[:2] == ["git", "fetch"]:
                return _make_result(1, stderr="fatal: remote not found")
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(
            _create_review_worktrees("42", "feature/test", repo)
        )

        assert result["success"] is False
        assert result["error_type"] == "fetch_failed"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_partial_failure_cleans_up(self, mock_run, tmp_path):
        """First 2 worktrees succeed, 3rd fails -- cleanup performed."""
        repo = _make_repo(tmp_path)
        worktree_add_count = {"n": 0}

        def side_effect(args, cwd=None, timeout=60):
            if args[:2] == ["git", "check-ignore"]:
                return _make_result(0)
            if args[:2] == ["git", "worktree"] and "prune" in args:
                return _make_result(0)
            if args[:2] == ["git", "fetch"]:
                return _make_result(0)
            if args[:2] == ["git", "worktree"] and "add" in args:
                worktree_add_count["n"] += 1
                path = args[3]
                if worktree_add_count["n"] <= 2:
                    os.makedirs(path, exist_ok=True)
                    return _make_result(0)
                else:
                    return _make_result(1, stderr="worktree add failed")
            # worktree remove (cleanup of partial)
            if args[:2] == ["git", "worktree"] and "remove" in args:
                return _make_result(0)
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(
            _create_review_worktrees("42", "feature/test", repo)
        )

        assert result["success"] is False
        assert result["error_type"] == "worktree_creation_failed"
        assert result["cleanup_performed"] is True

    def test_invalid_branch_name(self, tmp_path):
        """Shell metacharacters in branch name -- validation error."""
        repo = _make_repo(tmp_path)

        result = asyncio.run(
            _create_review_worktrees("42", "main; rm -rf /", repo)
        )

        assert result["success"] is False
        assert result["error_type"] == "validation_error"


# ── _cleanup_review_worktrees Tests ──────────────────────


class TestCleanupReviewWorktrees:
    """Tests for _cleanup_review_worktrees."""

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_removes_existing_worktrees(self, mock_run, tmp_path):
        """3 worktree dirs exist -- all should be removed."""
        repo = _make_repo(tmp_path)
        worktrees_dir = os.path.join(repo, ".worktrees")
        os.makedirs(worktrees_dir, exist_ok=True)

        # Create 3 worktree directories
        for wt_type in ["analyst", "codebase", "security"]:
            wt_path = os.path.join(worktrees_dir, f"omni-{wt_type}-42")
            os.makedirs(wt_path)

        def side_effect(args, cwd=None, timeout=60):
            # git worktree remove -- actually delete the dir
            if args[:2] == ["git", "worktree"] and "remove" in args:
                path = args[3]
                if os.path.exists(path):
                    shutil.rmtree(path)
                return _make_result(0)
            # git worktree prune
            if args[:2] == ["git", "worktree"] and "prune" in args:
                return _make_result(0)
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(
            _cleanup_review_worktrees("42", repo)
        )

        assert result["success"] is True
        assert len(result["removed"]) == 3

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_already_clean(self, mock_run, tmp_path):
        """No worktree dirs exist -- all reported as already_clean."""
        repo = _make_repo(tmp_path)
        # Ensure .worktrees dir exists but is empty
        worktrees_dir = os.path.join(repo, ".worktrees")
        os.makedirs(worktrees_dir, exist_ok=True)

        def side_effect(args, cwd=None, timeout=60):
            if args[:2] == ["git", "worktree"] and "prune" in args:
                return _make_result(0)
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(
            _cleanup_review_worktrees("42", repo)
        )

        assert result["success"] is True
        assert len(result["already_clean"]) == 3
        assert len(result["removed"]) == 0

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_rmtree_failure_reports_error(self, mock_run, tmp_path):
        """When git worktree remove fails AND shutil.rmtree fails, error is reported."""
        repo = _make_repo(tmp_path)
        worktrees_dir = os.path.join(repo, ".worktrees")
        os.makedirs(worktrees_dir, exist_ok=True)

        # Create 1 worktree dir that will be stubborn
        stubborn = os.path.join(worktrees_dir, "omni-analyst-42")
        os.makedirs(stubborn)

        def side_effect(args, cwd=None, timeout=60):
            if args[:2] == ["git", "worktree"] and "remove" in args:
                # Simulate git worktree remove failing (dir still exists)
                return _make_result(1, stderr="error: failed to remove")
            if args[:2] == ["git", "worktree"] and "prune" in args:
                return _make_result(0)
            return _make_result(0)

        mock_run.side_effect = side_effect

        # Patch shutil.rmtree to raise an exception
        with patch("omniforge_mcp_server.shutil.rmtree", side_effect=PermissionError("Permission denied")):
            result = asyncio.run(
                _cleanup_review_worktrees("42", repo)
            )

        assert result["success"] is False
        assert len(result["errors"]) >= 1
        assert "omni-analyst-42" in result["errors"][0]

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_gitignore_updated_when_not_present(self, mock_run, tmp_path):
        """When .worktrees/ is not gitignored, it gets added to .gitignore."""
        repo = _make_repo(tmp_path)
        gitignore = os.path.join(repo, ".gitignore")
        with open(gitignore, "w") as f:
            f.write("node_modules/\n")

        call_log = []

        def side_effect(args, cwd=None, timeout=60):
            call_log.append(args)
            cmd = " ".join(args)
            if "check-ignore" in cmd:
                return _make_result(1)  # NOT ignored
            if "prune" in cmd:
                return _make_result(0)
            if "fetch" in cmd:
                return _make_result(0)
            if "worktree" in cmd and "add" in cmd:
                path = args[3]
                os.makedirs(path, exist_ok=True)
                return _make_result(0)
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(
            _create_review_worktrees("42", "feature/test", repo)
        )

        assert result["success"] is True
        # Verify .gitignore was updated
        with open(gitignore) as f:
            content = f.read()
        assert ".worktrees/" in content
        assert content.endswith("\n")  # properly terminated
