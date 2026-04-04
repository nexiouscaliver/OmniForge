"""Tests for OmniFix worktree cleanup in OmniForge MCP server."""

import asyncio
import os
import shutil
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from omniforge_mcp_server import (  # noqa: E402
    _cleanup_omnifix_worktrees,
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


# ── _cleanup_omnifix_worktrees Tests ────────────────────


class TestCleanupOmnifixWorktrees:
    """Tests for _cleanup_omnifix_worktrees."""

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_cleanup_fix_worktree(self, mock_run, tmp_path):
        """Fix worktree .worktrees/omnifix-42 exists -- should be removed."""
        repo = _make_repo(tmp_path)
        worktrees_dir = os.path.join(repo, ".worktrees")
        os.makedirs(worktrees_dir, exist_ok=True)

        fix_path = os.path.join(worktrees_dir, "omnifix-42")
        os.makedirs(fix_path)

        def side_effect(args, cwd=None, timeout=60):
            # git worktree remove -- actually delete the dir
            if args[:2] == ["git", "worktree"] and "remove" in args:
                path = args[3]
                if os.path.exists(path):
                    shutil.rmtree(path)
                return _make_result(0)
            # git branch -D -- ignore
            if args[:2] == ["git", "branch"]:
                return _make_result(0)
            # git worktree prune
            if args[:2] == ["git", "worktree"] and "prune" in args:
                return _make_result(0)
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(
            _cleanup_omnifix_worktrees("42", repo)
        )

        assert result["success"] is True
        assert "omnifix-42" in result["removed"]
        assert result["mr_id"] == "42"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_cleanup_triage_worktrees(self, mock_run, tmp_path):
        """Triage worktrees omnifix-triage-42-0 and -1 exist -- both removed."""
        repo = _make_repo(tmp_path)
        worktrees_dir = os.path.join(repo, ".worktrees")
        os.makedirs(worktrees_dir, exist_ok=True)

        # Create triage worktree dirs
        for i in range(2):
            triage_path = os.path.join(worktrees_dir, f"omnifix-triage-42-{i}")
            os.makedirs(triage_path)

        def side_effect(args, cwd=None, timeout=60):
            if args[:2] == ["git", "worktree"] and "remove" in args:
                path = args[3]
                if os.path.exists(path):
                    shutil.rmtree(path)
                return _make_result(0)
            if args[:2] == ["git", "branch"]:
                return _make_result(0)
            if args[:2] == ["git", "worktree"] and "prune" in args:
                return _make_result(0)
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(
            _cleanup_omnifix_worktrees("42", repo)
        )

        assert result["success"] is True
        assert "omnifix-triage-42-0" in result["removed"]
        assert "omnifix-triage-42-1" in result["removed"]

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_cleanup_temp_branch(self, mock_run, tmp_path):
        """Verify git branch -D omnifix-temp-42 is called."""
        repo = _make_repo(tmp_path)

        call_log = []

        def side_effect(args, cwd=None, timeout=60):
            call_log.append(args)
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(
            _cleanup_omnifix_worktrees("42", repo)
        )

        # Find the git branch -D call
        branch_calls = [
            c for c in call_log
            if c[:2] == ["git", "branch"] and "-D" in c
        ]
        assert len(branch_calls) == 1
        assert branch_calls[0] == ["git", "branch", "-D", "omnifix-temp-42"]

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_already_clean(self, mock_run, tmp_path):
        """No dirs exist -- omnifix-42 in already_clean, removed is empty."""
        repo = _make_repo(tmp_path)

        def side_effect(args, cwd=None, timeout=60):
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(
            _cleanup_omnifix_worktrees("42", repo)
        )

        assert result["success"] is True
        assert "omnifix-42" in result["already_clean"]
        assert len(result["removed"]) == 0

    def test_invalid_mr_id(self, tmp_path):
        """Non-numeric MR ID -- should return validation_error."""
        repo = _make_repo(tmp_path)

        result = asyncio.run(
            _cleanup_omnifix_worktrees("abc", repo)
        )

        assert result["success"] is False
        assert result["error_type"] == "validation_error"
