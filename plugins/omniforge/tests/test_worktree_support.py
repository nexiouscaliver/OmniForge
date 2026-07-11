"""Tests for worktree support in OmniForge MCP server.

Covers _resolve_main_repo_root, worktree-aware create/cleanup operations,
and verification that git commands use the main repo root as cwd.
"""

import asyncio
import os
import shutil
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from omniforge_mcp_server import (  # noqa: E402
    _cleanup_omnifix_worktrees,
    _cleanup_review_worktrees,
    _create_review_worktrees,
    _resolve_main_repo_root,
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


def _make_main_repo(tmp_path, name="main-repo"):
    """Create a fake main git repo directory (has .git/ dir) and return its path."""
    repo = tmp_path / name
    repo.mkdir()
    (repo / ".git").mkdir()
    return str(repo)


def _make_worktree(tmp_path, main_repo_path, name="worktree-1"):
    """Create a fake linked worktree (has .git FILE pointing to main repo).

    A real git worktree has a .git *file* (not directory) whose content is:
        gitdir: /path/to/main-repo/.git/worktrees/<name>
    """
    wt = tmp_path / name
    wt.mkdir()
    git_file = wt / ".git"
    git_file.write_text(f"gitdir: {main_repo_path}/.git/worktrees/{name}\n")
    return str(wt)


def _rev_parse_side_effect(main_repo):
    """Return a side_effect callable that answers rev-parse with main_repo/.git."""
    def _side(args, cwd=None, timeout=60):
        if args == ["git", "rev-parse", "--git-common-dir"]:
            return _make_result(0, f"{main_repo}/.git\n")
        return _make_result(0)
    return _side


def _create_side_effect(main_repo):
    """Return a side_effect that answers rev-parse + creates worktree dirs."""
    def _side(args, cwd=None, timeout=60):
        if args == ["git", "rev-parse", "--git-common-dir"]:
            return _make_result(0, f"{main_repo}/.git\n")
        if args[:2] == ["git", "check-ignore"]:
            return _make_result(0)
        if args[:2] == ["git", "worktree"] and "prune" in args:
            return _make_result(0)
        if args[:2] == ["git", "fetch"]:
            return _make_result(0)
        if args[:2] == ["git", "worktree"] and "add" in args:
            path = args[3]
            os.makedirs(path, exist_ok=True)
            return _make_result(0)
        if args[:2] == ["git", "worktree"] and "remove" in args:
            path = args[3]
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)
            return _make_result(0)
        return _make_result(0)
    return _side


def _cleanup_side_effect(main_repo):
    """Return a side_effect that answers rev-parse + simulates worktree remove."""
    def _side(args, cwd=None, timeout=60):
        if args == ["git", "rev-parse", "--git-common-dir"]:
            return _make_result(0, f"{main_repo}/.git\n")
        if args[:2] == ["git", "worktree"] and "remove" in args:
            path = args[3]
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)
            return _make_result(0)
        if args[:2] == ["git", "worktree"] and "prune" in args:
            return _make_result(0)
        if args[:2] == ["git", "branch"]:
            return _make_result(0)
        return _make_result(0)
    return _side


# ── _resolve_main_repo_root Tests ────────────────────────


class TestResolveMainRepoRoot:
    """Tests for _resolve_main_repo_root."""

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_regular_repo_returns_same_path(self, mock_run, tmp_path):
        """For a main repo, rev-parse returns <repo>/.git → dirname == repo itself."""
        repo = _make_main_repo(tmp_path)
        mock_run.return_value = _make_result(0, f"{repo}/.git\n")

        result = asyncio.run(_resolve_main_repo_root(repo))

        assert os.path.abspath(result) == os.path.abspath(repo)

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_worktree_returns_main_repo_root(self, mock_run, tmp_path):
        """For a linked worktree, rev-parse returns main/.git → returns main repo path."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)
        mock_run.return_value = _make_result(0, f"{main_repo}/.git\n")

        result = asyncio.run(_resolve_main_repo_root(worktree))

        assert os.path.abspath(result) == os.path.abspath(main_repo)
        assert os.path.abspath(result) != os.path.abspath(worktree)

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_rev_parse_failure_returns_repo_root(self, mock_run, tmp_path):
        """When git rev-parse fails (returncode != 0), falls back gracefully."""
        repo = _make_main_repo(tmp_path)
        mock_run.return_value = _make_result(1, stderr="fatal: not a git repo")

        result = asyncio.run(_resolve_main_repo_root(repo))

        assert result == repo

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_strips_dot_git_suffix(self, mock_run, tmp_path):
        """rev-parse output ending in /.git → strips to parent dir (the main repo root)."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)
        mock_run.return_value = _make_result(0, f"{main_repo}/.git\n")

        result = asyncio.run(_resolve_main_repo_root(worktree))

        assert result.endswith("main-repo")
        assert not result.endswith(".git")

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_handles_trailing_whitespace_in_stdout(self, mock_run, tmp_path):
        """rev-parse output with trailing newlines/whitespace is handled correctly."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)
        mock_run.return_value = _make_result(0, f"{main_repo}/.git\n\n")

        result = asyncio.run(_resolve_main_repo_root(worktree))

        assert os.path.abspath(result) == os.path.abspath(main_repo)

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_rev_parse_called_with_correct_args(self, mock_run, tmp_path):
        """Verify git rev-parse --git-common-dir is the command used."""
        repo = _make_main_repo(tmp_path)
        mock_run.return_value = _make_result(0, f"{repo}/.git\n")

        asyncio.run(_resolve_main_repo_root(repo))

        mock_run.assert_called_once_with(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=repo,
            timeout=10,
        )

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_rev_parse_cwd_is_repo_root(self, mock_run, tmp_path):
        """rev-parse is called with cwd set to the passed-in repo_root (the worktree)."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)
        mock_run.return_value = _make_result(0, f"{main_repo}/.git\n")

        asyncio.run(_resolve_main_repo_root(worktree))

        call_args = mock_run.call_args
        assert call_args.kwargs.get("cwd", call_args[1].get("cwd")) == worktree


# ── _create_review_worktrees Worktree Support ────────────


class TestCreateReviewWorktreesWorktree:
    """Tests that _create_review_worktrees resolves worktree paths to main repo root."""

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_worktree_path_resolves_to_main_repo(self, mock_run, tmp_path):
        """When called from a worktree path, worktrees are created under main repo's .worktrees/."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)
        mock_run.side_effect = _create_side_effect(main_repo)

        result = asyncio.run(
            _create_review_worktrees("42", "feature/test", worktree)
        )

        assert result["success"] is True
        assert len(result["worktrees"]) == 3
        # All worktree paths must be under main_repo/.worktrees/
        for wt_type, path in result["worktrees"].items():
            assert main_repo in path, \
                f"{wt_type} path {path} not under main repo {main_repo}"
            assert ".worktrees" in path

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_git_commands_use_main_repo_cwd(self, mock_run, tmp_path):
        """All git commands (except rev-parse) must use the main repo root as cwd."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)
        call_log = []

        original_side = _create_side_effect(main_repo)

        def logging_side(args, cwd=None, timeout=60):
            call_log.append((list(args), cwd))
            return original_side(args, cwd=cwd, timeout=timeout)

        mock_run.side_effect = logging_side

        asyncio.run(
            _create_review_worktrees("42", "feature/test", worktree)
        )

        # Every git command that is NOT rev-parse must use main_repo as cwd
        for args, cwd in call_log:
            if args == ["git", "rev-parse", "--git-common-dir"]:
                continue
            assert os.path.abspath(cwd) == os.path.abspath(main_repo), \
                f"git command {args} used cwd={cwd}, expected {main_repo}"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_worktrees_dir_created_at_main_repo(self, mock_run, tmp_path):
        """The .worktrees/ directory is created at the main repo root, not the worktree."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)
        mock_run.side_effect = _create_side_effect(main_repo)

        asyncio.run(
            _create_review_worktrees("42", "feature/test", worktree)
        )

        assert os.path.isdir(os.path.join(main_repo, ".worktrees"))
        # The worktree path itself should NOT have a .worktrees dir
        assert not os.path.isdir(os.path.join(worktree, ".worktrees"))

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_gitignore_targets_main_repo(self, mock_run, tmp_path):
        """The .gitignore of the main repo (not the worktree) is modified."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)

        # Create .gitignore in main repo
        main_gitignore = os.path.join(main_repo, ".gitignore")
        with open(main_gitignore, "w") as f:
            f.write("node_modules/\n")

        def side_effect(args, cwd=None, timeout=60):
            if args == ["git", "rev-parse", "--git-common-dir"]:
                return _make_result(0, f"{main_repo}/.git\n")
            if args[:2] == ["git", "check-ignore"]:
                return _make_result(1)  # NOT ignored → triggers .gitignore update
            if args[:2] == ["git", "worktree"] and "prune" in args:
                return _make_result(0)
            if args[:2] == ["git", "fetch"]:
                return _make_result(0)
            if args[:2] == ["git", "worktree"] and "add" in args:
                path = args[3]
                os.makedirs(path, exist_ok=True)
                return _make_result(0)
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(
            _create_review_worktrees("42", "feature/test", worktree)
        )

        assert result["success"] is True
        # .gitignore at main repo should now contain .worktrees/
        with open(main_gitignore) as f:
            content = f.read()
        assert ".worktrees/" in content

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_rev_parse_called_with_worktree_cwd(self, mock_run, tmp_path):
        """The initial rev-parse call uses the worktree path as cwd."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)
        mock_run.side_effect = _create_side_effect(main_repo)

        asyncio.run(
            _create_review_worktrees("42", "feature/test", worktree)
        )

        # Find the rev-parse call and verify its cwd
        rev_parse_calls = [
            c for c in mock_run.call_args_list
            if c.args and c.args[0] == ["git", "rev-parse", "--git-common-dir"]
        ]
        assert len(rev_parse_calls) == 1
        # cwd is passed as keyword argument
        assert rev_parse_calls[0].kwargs.get("cwd") == worktree


# ── _cleanup_review_worktrees Worktree Support ───────────


class TestCleanupReviewWorktreesWorktree:
    """Tests that _cleanup_review_worktrees resolves worktree paths to main repo root."""

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_git_commands_use_main_repo_cwd(self, mock_run, tmp_path):
        """All git commands during cleanup use the main repo root as cwd."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)

        # Create worktree dirs under main repo
        worktrees_dir = os.path.join(main_repo, ".worktrees")
        os.makedirs(worktrees_dir, exist_ok=True)
        for wt_type in ["analyst", "codebase", "security"]:
            os.makedirs(os.path.join(worktrees_dir, f"omni-{wt_type}-42"))

        call_log = []
        original_side = _cleanup_side_effect(main_repo)

        def logging_side(args, cwd=None, timeout=60):
            call_log.append((list(args), cwd))
            return original_side(args, cwd=cwd, timeout=timeout)

        mock_run.side_effect = logging_side

        result = asyncio.run(
            _cleanup_review_worktrees("42", worktree)
        )

        assert result["success"] is True
        assert len(result["removed"]) == 3

        # Every git command that is NOT rev-parse must use main_repo as cwd
        for args, cwd in call_log:
            if args == ["git", "rev-parse", "--git-common-dir"]:
                continue
            assert os.path.abspath(cwd) == os.path.abspath(main_repo), \
                f"git command {args} used cwd={cwd}, expected {main_repo}"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_cleanup_removes_worktrees_at_main_repo(self, mock_run, tmp_path):
        """Worktree dirs under main repo's .worktrees/ are removed."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)

        worktrees_dir = os.path.join(main_repo, ".worktrees")
        os.makedirs(worktrees_dir, exist_ok=True)
        for wt_type in ["analyst", "codebase", "security"]:
            os.makedirs(os.path.join(worktrees_dir, f"omni-{wt_type}-42"))

        mock_run.side_effect = _cleanup_side_effect(main_repo)

        result = asyncio.run(
            _cleanup_review_worktrees("42", worktree)
        )

        assert result["success"] is True
        assert len(result["removed"]) == 3


# ── _cleanup_omnifix_worktrees Worktree Support ──────────


class TestCleanupOmnifixWorktreesWorktree:
    """Tests that _cleanup_omnifix_worktrees resolves worktree paths to main repo root."""

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_git_commands_use_main_repo_cwd(self, mock_run, tmp_path):
        """All git commands during omnifix cleanup use the main repo root as cwd."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)

        # Create fix + triage worktree dirs under main repo
        worktrees_dir = os.path.join(main_repo, ".worktrees")
        os.makedirs(worktrees_dir, exist_ok=True)
        os.makedirs(os.path.join(worktrees_dir, "omnifix-42"))
        os.makedirs(os.path.join(worktrees_dir, "omnifix-triage-42-0"))
        os.makedirs(os.path.join(worktrees_dir, "omnifix-triage-42-1"))

        call_log = []
        original_side = _cleanup_side_effect(main_repo)

        def logging_side(args, cwd=None, timeout=60):
            call_log.append((list(args), cwd))
            return original_side(args, cwd=cwd, timeout=timeout)

        mock_run.side_effect = logging_side

        result = asyncio.run(
            _cleanup_omnifix_worktrees("42", worktree)
        )

        assert result["success"] is True

        # Every git command that is NOT rev-parse must use main_repo as cwd
        for args, cwd in call_log:
            if args == ["git", "rev-parse", "--git-common-dir"]:
                continue
            assert os.path.abspath(cwd) == os.path.abspath(main_repo), \
                f"git command {args} used cwd={cwd}, expected {main_repo}"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_branch_delete_uses_main_repo_cwd(self, mock_run, tmp_path):
        """The git branch -D temp branch command uses main repo root as cwd."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)

        call_log = []
        original_side = _cleanup_side_effect(main_repo)

        def logging_side(args, cwd=None, timeout=60):
            call_log.append((list(args), cwd))
            return original_side(args, cwd=cwd, timeout=timeout)

        mock_run.side_effect = logging_side

        asyncio.run(
            _cleanup_omnifix_worktrees("42", worktree)
        )

        # Find git branch -D call and verify its cwd
        branch_calls = [
            (a, c) for a, c in call_log
            if a[:2] == ["git", "branch"] and "-D" in a
        ]
        assert len(branch_calls) == 1
        assert os.path.abspath(branch_calls[0][1]) == os.path.abspath(main_repo)


# ── Integration-style Tests ───────────────────────────────


class TestWorktreeIntegration:
    """Integration-style tests for the full worktree flow."""

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_full_create_flow_from_worktree(self, mock_run, tmp_path):
        """Simulate calling _create_review_worktrees with a worktree path.

        Verify:
        - rev-parse is called first with the worktree path
        - All subsequent git commands use the main repo root as cwd
        - Returned worktree paths are under <main_repo>/.worktrees/
        """
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)

        call_log = []
        original_side = _create_side_effect(main_repo)

        def logging_side(args, cwd=None, timeout=60):
            call_log.append({
                "args": list(args),
                "cwd": cwd,
                "timeout": timeout,
            })
            return original_side(args, cwd=cwd, timeout=timeout)

        mock_run.side_effect = logging_side

        result = asyncio.run(
            _create_review_worktrees("42", "feature/test", worktree)
        )

        assert result["success"] is True
        assert result["mr_id"] == "42"

        # 1. Verify rev-parse is the first call
        assert call_log[0]["args"] == ["git", "rev-parse", "--git-common-dir"]
        assert call_log[0]["cwd"] == worktree

        # 2. Verify all returned worktree paths are absolute and under main repo
        main_abs = os.path.abspath(main_repo)
        for wt_type, path in result["worktrees"].items():
            assert os.path.isabs(path), f"{wt_type} path not absolute: {path}"
            assert os.path.abspath(path).startswith(main_abs), \
                f"{wt_type} path {path} not under main repo {main_abs}"

        # 3. Verify fetch was called with main repo as cwd
        fetch_calls = [c for c in call_log if c["args"][:2] == ["git", "fetch"]]
        assert len(fetch_calls) == 1
        assert os.path.abspath(fetch_calls[0]["cwd"]) == main_abs

        # 4. Verify worktree add commands used main repo as cwd
        add_calls = [c for c in call_log if c["args"][:2] == ["git", "worktree"]
                     and "add" in c["args"]]
        assert len(add_calls) == 3  # 3 worktrees
        for call in add_calls:
            assert os.path.abspath(call["cwd"]) == main_abs

        # 5. Verify all worktree add paths are under main_repo/.worktrees/
        expected_prefix = os.path.join(main_abs, ".worktrees")
        for call in add_calls:
            wt_path = call["args"][3]
            assert os.path.abspath(wt_path).startswith(expected_prefix), \
                f"worktree add path {wt_path} not under {expected_prefix}"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_full_create_then_cleanup_from_worktree(self, mock_run, tmp_path):
        """Create then cleanup from a worktree path — both use main repo root."""
        main_repo = _make_main_repo(tmp_path)
        worktree = _make_worktree(tmp_path, main_repo)

        # Phase 1: Create
        mock_run.side_effect = _create_side_effect(main_repo)
        create_result = asyncio.run(
            _create_review_worktrees("99", "feature/integration", worktree)
        )
        assert create_result["success"] is True

        # Worktree dirs now exist under main_repo/.worktrees/
        worktrees_dir = os.path.join(main_repo, ".worktrees")
        assert os.path.isdir(worktrees_dir)

        # Phase 2: Cleanup — side_effect must also handle rev-parse
        mock_run.side_effect = _cleanup_side_effect(main_repo)
        cleanup_result = asyncio.run(
            _cleanup_review_worktrees("99", worktree)
        )
        assert cleanup_result["success"] is True
        assert len(cleanup_result["removed"]) == 3
