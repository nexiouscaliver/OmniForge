"""Tests for OmniForge MCP server input validation functions."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from omniforge_mcp_server import (
    validate_branch_name,
    validate_mr_id,
    validate_repo_root,
)


# ── validate_mr_id ────────────────────────────────────────


class TestValidateMrId:
    def test_valid_numeric(self):
        assert validate_mr_id("136") == "136"

    def test_strips_exclamation(self):
        assert validate_mr_id("!136") == "136"

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="Invalid MR ID"):
            validate_mr_id("abc")

    def test_rejects_injection(self):
        with pytest.raises(ValueError, match="Invalid MR ID"):
            validate_mr_id("136; rm -rf /")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid MR ID"):
            validate_mr_id("")


# ── validate_repo_root ────────────────────────────────────


class TestValidateRepoRoot:
    def test_valid_git_repo(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        assert validate_repo_root(str(tmp_path)) == str(tmp_path)

    def test_valid_git_worktree(self, tmp_path):
        git_file = tmp_path / ".git"
        git_file.write_text("gitdir: /tmp/main/.git/worktrees/review-1\n")
        assert validate_repo_root(str(tmp_path)) == str(tmp_path)

    def test_rejects_relative_path(self):
        with pytest.raises(ValueError, match="repo_root must be absolute"):
            validate_repo_root("relative/path")

    def test_rejects_non_git_dir(self, tmp_path):
        with pytest.raises(ValueError, match="Not a git repository"):
            validate_repo_root(str(tmp_path))


# ── validate_branch_name ──────────────────────────────────


class TestValidateBranchName:
    def test_valid_feature_branch(self):
        assert validate_branch_name("feature/my-branch") == "feature/my-branch"

    def test_valid_with_dots(self):
        assert validate_branch_name("release/v1.2.3") == "release/v1.2.3"

    def test_rejects_semicolon(self):
        with pytest.raises(ValueError, match="Invalid branch name"):
            validate_branch_name("branch; rm -rf /")

    def test_rejects_pipe(self):
        with pytest.raises(ValueError, match="Invalid branch name"):
            validate_branch_name("branch|evil")

    def test_rejects_backtick(self):
        with pytest.raises(ValueError, match="Invalid branch name"):
            validate_branch_name("branch`whoami`")
