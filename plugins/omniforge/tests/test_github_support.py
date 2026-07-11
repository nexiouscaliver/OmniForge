"""Tests for GitHub support functions: platform detection, PR data fetch,
review posting (summary + inline + full), PR creation, discussions, and replies."""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from omniforge_mcp_server import (  # noqa: E402
    _detect_platform,
    _get_github_repo_id,
    _fetch_pr_data,
    _post_pr_review_summary,
    _post_pr_inline_thread,
    _post_pr_full_review,
    _create_github_pr,
    _fetch_pr_discussions,
    _reply_to_pr_comment,
)


# ── Helpers ────────────────────────────────────────────────


def _make_result(returncode=0, stdout="", stderr=""):
    """Create a mock result matching run_exec's return shape."""
    class R:
        pass
    r = R()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _make_repo(tmp_path):
    """Create a fake git repo directory and return its path."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    os.makedirs(os.path.join(repo, ".git"))
    return repo


# ── Sample Data ────────────────────────────────────────────

SAMPLE_PR_JSON = json.dumps({
    "title": "feat: add GitHub support",
    "body": "This PR adds GitHub support.",
    "headRefName": "feature/github",
    "baseRefName": "main",
    "state": "OPEN",
    "author": {"login": "shahilkadia"},
    "labels": [{"name": "enhancement"}, {"name": "review"}],
    "assignees": [{"login": "dev1"}],
    "reviewRequests": [{"login": "reviewer1"}],
    "comments": [
        {
            "author": {"login": "reviewer1"},
            "body": "Looks good overall.",
        },
        {
            "author": {"login": "dev1"},
            "body": "Thanks!",
        },
    ],
})

SAMPLE_PR_DIFF = "+++ b/file1.py\n@@ -1 +1 @@\n-old\n+new\n+++ b/file2.py\n"

SAMPLE_PR_COMMITS = "abc1234 feat: add github support\ndef5678 fix: minor issue"

SAMPLE_PR_COMMENTS_VIEW = json.dumps([
    {"author": {"login": "reviewer1"}, "body": "Looks good overall."},
    {"author": {"login": "dev1"}, "body": "Thanks!"},
])


def _build_pr_side_effects(
    auth_rc=0,
    pr_view_rc=0,
    pr_view_stdout=None,
    diff_rc=0,
    diff_stdout=None,
    commits_rc=0,
    commits_stdout=None,
):
    """Build an ordered list of side effects for run_exec calls in _fetch_pr_data.

    Call order in _fetch_pr_data:
      1. gh auth status
      2. gh pr view <id> --json ...
      3. gh pr diff <id>
      4. gh pr view <id> --json commits ...
    """
    if pr_view_stdout is None:
        pr_view_stdout = SAMPLE_PR_JSON
    if diff_stdout is None:
        diff_stdout = SAMPLE_PR_DIFF
    if commits_stdout is None:
        commits_stdout = SAMPLE_PR_COMMITS

    return [
        _make_result(auth_rc),                              # 1. auth
        _make_result(pr_view_rc, pr_view_stdout),           # 2. pr view json
        _make_result(diff_rc, diff_stdout),                 # 3. pr diff
        _make_result(commits_rc, commits_stdout),           # 4. pr view commits
    ]


SAMPLE_PR_DISCUSSIONS = json.dumps([
    {
        "id": 1001,
        "path": "src/app.py",
        "line": 42,
        "original_line": 42,
        "body": "**Important** — Missing null check",
        "user": {"login": "reviewer1"},
        "created_at": "2026-03-26T01:30:00Z",
        "in_reply_to_id": None,
    },
    {
        "id": 1002,
        "path": "src/app.py",
        "line": 42,
        "original_line": 42,
        "body": "Will fix",
        "user": {"login": "dev1"},
        "created_at": "2026-03-26T02:00:00Z",
        "in_reply_to_id": 1001,
    },
    {
        "id": 1003,
        "path": "src/utils.py",
        "line": 10,
        "original_line": 10,
        "body": "**Minor** — Consider renaming",
        "user": {"login": "reviewer1"},
        "created_at": "2026-03-26T01:30:00Z",
        "in_reply_to_id": None,
    },
    {
        "id": 1004,
        "body": "## OmniForge\n\n**Verdict:** APPROVE_WITH_FIXES",
        "user": {"login": "shahilkadia"},
        "created_at": "2026-03-26T01:00:00Z",
        "in_reply_to_id": None,
    },
])


# ── _detect_platform Tests ────────────────────────────────


class TestDetectPlatform:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_github_remote_detected(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(
            0, "origin  github.com:owner/repo.git (fetch)\norigin  github.com:owner/repo.git (push)"
        )
        result = asyncio.run(_detect_platform(repo))
        assert result == "github"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_gitlab_remote_detected(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(
            0, "origin  git@gitlab.com:owner/repo.git (fetch)"
        )
        result = asyncio.run(_detect_platform(repo))
        assert result == "gitlab"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_self_hosted_gitlab_detected(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(
            0, "origin  git@gitlab.company.com:owner/repo.git (fetch)"
        )
        result = asyncio.run(_detect_platform(repo))
        assert result == "gitlab"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_unknown_remote_falls_back_to_auth(self, mock_run, tmp_path):
        """Remote doesn't match GitHub or GitLab — fall back to auth check."""
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # git remote -v
                return _make_result(0, "origin  bitbucket.org:owner/repo.git")
            if call_count == 2:  # gh auth status
                return _make_result(0)  # gh authenticated
            return _make_result(1)  # glab not checked

        mock_run.side_effect = side_effect
        result = asyncio.run(_detect_platform(repo))
        assert result == "github"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_git_remote_fails_falls_back_to_gh(self, mock_run, tmp_path):
        """git remote -v fails → check gh auth → returns 'github'."""
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # git remote -v
                return _make_result(1, stderr="not a git repo")
            if call_count == 2:  # gh auth status
                return _make_result(0)
            return _make_result(1)

        mock_run.side_effect = side_effect
        result = asyncio.run(_detect_platform(repo))
        assert result == "github"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_no_remote_no_auth_returns_unknown(self, mock_run, tmp_path):
        """git remote fails, gh not authed, glab not authed → 'unknown'."""
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(1, stderr="not authenticated")
        result = asyncio.run(_detect_platform(repo))
        assert result == "unknown"


# ── _get_github_repo_id Tests ─────────────────────────────


class TestGetGithubRepoId:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(0, "nexiouscaliver/OmniForge")
        result = asyncio.run(_get_github_repo_id(repo))
        assert result == "nexiouscaliver/OmniForge"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_failure_returns_empty(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(1, stderr="not found")
        result = asyncio.run(_get_github_repo_id(repo))
        assert result == ""


# ── _fetch_pr_data Tests ──────────────────────────────────


class TestFetchPrData:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.side_effect = _build_pr_side_effects()

        result = asyncio.run(_fetch_pr_data("42", repo))

        assert result["success"] is True
        assert result["pr_id"] == "42"
        assert result["title"] == "feat: add GitHub support"
        assert result["author"] == "shahilkadia"
        assert result["source_branch"] == "feature/github"
        assert result["target_branch"] == "main"
        assert result["state"] == "OPEN"
        assert result["description"] == "This PR adds GitHub support."
        assert result["files_changed"] == ["file1.py", "file2.py"]
        assert result["labels"] == ["enhancement", "review"]
        assert result["assignees"] == ["dev1"]
        assert result["reviewers"] == ["reviewer1"]

        # Commits parsed correctly
        assert len(result["commits"]) == 2
        assert result["commits"][0]["sha"] == "abc1234"
        assert result["commits"][0]["message"] == "feat: add github support"
        assert result["commits"][1]["sha"] == "def5678"

        # Diff present
        assert "file1.py" in result["diff"]
        assert result["diff_truncated"] is False

        # Comments built from metadata
        assert "reviewer1" in result["comments"]
        assert "Looks good overall." in result["comments"]
        assert "dev1" in result["comments"]
        assert "Thanks!" in result["comments"]

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_auth_failure(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(1, stderr="not logged in")

        result = asyncio.run(_fetch_pr_data("42", repo))
        assert result["success"] is False
        assert result["error_type"] == "auth_failure"
        assert "gh not authenticated" in result["error"]

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_pr_not_found(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0),                          # auth OK
            _make_result(1, stderr="not found"),      # pr view fails
        ]

        result = asyncio.run(_fetch_pr_data("999", repo))
        assert result["success"] is False
        assert result["error_type"] == "pr_not_found"
        assert "999" in result["error"]

    def test_invalid_pr_id(self, tmp_path):
        repo = _make_repo(tmp_path)
        result = asyncio.run(_fetch_pr_data("abc", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"
        assert "Invalid PR ID" in result["error"]

    def test_invalid_repo_root(self, tmp_path):
        result = asyncio.run(_fetch_pr_data("42", "relative/path"))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"
        assert "repo_root must be absolute" in result["error"]

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_output_dict_has_same_keys_as_mr_data(self, mock_run, tmp_path):
        """Output dict should have the same keys as _fetch_mr_data."""
        from omniforge_mcp_server import _fetch_mr_data
        repo = _make_repo(tmp_path)

        # PR data
        mock_run.side_effect = _build_pr_side_effects()
        pr_result = asyncio.run(_fetch_pr_data("42", repo))

        # MR data (mock with glab-style JSON)
        sample_mr_json = json.dumps({
            "title": "test",
            "source_branch": "feat",
            "target_branch": "main",
            "pipeline_status": "success",
            "description": "desc",
            "author": {"username": "user"},
            "labels": [],
            "assignees": [],
            "reviewers": [],
        })
        mock_run.side_effect = [
            _make_result(0),                                    # auth OK
            _make_result(0, sample_mr_json),                    # mr view
            _make_result(0, "comment text"),                    # comments
            _make_result(0, "+++ b/f.py\n@@ -1 +1 @@\n-a\n+b"), # diff
            _make_result(0),                                    # git fetch
            _make_result(0, "abc1234 test commit"),             # git log
        ]
        mr_result = asyncio.run(_fetch_mr_data("1", repo))

        # Compare keys (excluding platform-specific identifiers)
        # pipeline_status is GitLab-only (GitHub uses checks/runs differently)
        mr_keys = set(mr_result.keys()) - {"mr_id", "pipeline_status"}
        pr_keys = set(pr_result.keys()) - {"pr_id", "state"}
        assert pr_keys == mr_keys, (
            f"PR keys missing: {mr_keys - pr_keys}, "
            f"extra: {pr_keys - mr_keys}"
        )


# ── _post_pr_review_summary Tests ─────────────────────────


class TestPostPrReviewSummary:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(0)

        result = asyncio.run(_post_pr_review_summary("42", "## Review\nLooks good.", repo))
        assert result["success"] is True
        assert result["action"] == "summary_posted"
        assert result["pr_id"] == "42"

        # Verify gh pr comment was called
        call_args = mock_run.call_args[0][0]
        assert call_args[:3] == ["gh", "pr", "comment"]
        assert "42" in call_args
        assert "--body" in call_args
        assert "## Review\nLooks good." in call_args

    def test_empty_summary(self, tmp_path):
        repo = _make_repo(tmp_path)
        result = asyncio.run(_post_pr_review_summary("42", "", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_pr_id(self, tmp_path):
        repo = _make_repo(tmp_path)
        result = asyncio.run(_post_pr_review_summary("abc", "Summary", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_post_failure(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(1, stderr="403 Forbidden")

        result = asyncio.run(_post_pr_review_summary("42", "Summary text", repo))
        assert result["success"] is False
        assert result["error_type"] == "post_failed"


# ── _post_pr_inline_thread Tests ──────────────────────────


class TestPostPrInlineThread:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # gh repo view (repo_id fetch)
                return _make_result(0, "owner/repo")
            if call_count == 2:  # gh pr view --json headRefOid (SHA fetch)
                return _make_result(0, "abc123def456")
            return _make_result(0)  # gh api POST

        mock_run.side_effect = side_effect

        result = asyncio.run(_post_pr_inline_thread(
            "42", "src/app.py", 42, "**Important** — Missing null check", repo
        ))
        assert result["success"] is True
        assert result["file"] == "src/app.py"
        assert result["line"] == 42
        assert result["action"] == "inline_thread_posted"
        assert result["pr_id"] == "42"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_api_call_includes_commit_id_path_line_side(self, mock_run, tmp_path):
        """Verify the gh api command includes commit_id, path, line, side=RIGHT."""
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, "owner/repo")
            if call_count == 2:
                return _make_result(0, "abc123def456")
            return _make_result(0)

        mock_run.side_effect = side_effect

        asyncio.run(_post_pr_inline_thread(
            "42", "src/app.py", 42, "Body text", repo
        ))

        # The last call is the API POST
        api_call = mock_run.call_args_list[-1][0][0]
        assert api_call[:2] == ["gh", "api"]
        assert "repos/owner/repo/pulls/42/comments" in api_call[2]
        assert "--method" in api_call
        assert "POST" in api_call
        assert "--field" in api_call
        # Verify commit_id, path, line, side=RIGHT are in the fields
        fields = [api_call[i + 1] for i, a in enumerate(api_call) if a == "--field"]
        assert any("body=Body text" in f for f in fields)
        assert any("commit_id=abc123def456" in f for f in fields)
        assert any("path=src/app.py" in f for f in fields)
        assert any("line=42" in f for f in fields)
        assert any("side=RIGHT" in f for f in fields)

    def test_empty_body(self, tmp_path):
        repo = _make_repo(tmp_path)
        result = asyncio.run(_post_pr_inline_thread("42", "file.py", 10, "", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_line_number(self, tmp_path):
        repo = _make_repo(tmp_path)
        result = asyncio.run(_post_pr_inline_thread("42", "file.py", 0, "Body", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_api_failure(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, "owner/repo")
            if call_count == 2:
                return _make_result(0, "abc123def456")
            return _make_result(1, stderr="422 Validation Failed")

        mock_run.side_effect = side_effect

        result = asyncio.run(_post_pr_inline_thread(
            "42", "file.py", 10, "Body text", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "post_failed"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_repo_id_not_found(self, mock_run, tmp_path):
        """If repo_id can't be determined, returns repo_not_found."""
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(1, stderr="not found")

        result = asyncio.run(_post_pr_inline_thread(
            "42", "file.py", 10, "Body text", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "repo_not_found"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_head_sha_not_found(self, mock_run, tmp_path):
        """If head SHA can't be fetched, returns pr_not_found."""
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # repo_id OK
                return _make_result(0, "owner/repo")
            return _make_result(1, stderr="not found")  # head SHA fails

        mock_run.side_effect = side_effect

        result = asyncio.run(_post_pr_inline_thread(
            "42", "file.py", 10, "Body text", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "pr_not_found"


# ── _post_pr_full_review Tests ────────────────────────────


class TestPostPrFullReview:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success_summary_and_threads(self, mock_run, tmp_path):
        """Posts summary + 2 inline comments."""
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            cmd = " ".join(args)
            # Summary post
            if "comment" in cmd and "--body" in cmd:
                return _make_result(0)
            # repo_id fetch (called by inline thread)
            if "repo" in cmd and "view" in cmd and "nameWithOwner" in cmd:
                return _make_result(0, "owner/repo")
            # head SHA fetch
            if "pr" in cmd and "view" in cmd and "headRefOid" in cmd:
                return _make_result(0, "abc123")
            # API POST for inline thread
            return _make_result(0)

        mock_run.side_effect = side_effect

        findings = [
            {"file_path": "a.py", "line_number": 10, "body": "Issue 1"},
            {"file_path": "b.py", "line_number": 20, "body": "Issue 2"},
        ]
        result = asyncio.run(_post_pr_full_review("42", "Summary text", findings, repo))
        assert result["success"] is True
        assert result["summary_posted"] is True
        assert result["threads_posted"] == 2
        assert result["threads_total"] == 2
        assert result["errors"] == []

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_summary_fails_threads_succeed(self, mock_run, tmp_path):
        """Summary fails but threads succeed → errors list has summary error."""
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            cmd = " ".join(args)
            # Summary post fails
            if "comment" in cmd and "--body" in cmd:
                return _make_result(1, stderr="rate limit")
            if "repo" in cmd and "view" in cmd and "nameWithOwner" in cmd:
                return _make_result(0, "owner/repo")
            if "pr" in cmd and "view" in cmd and "headRefOid" in cmd:
                return _make_result(0, "abc123")
            return _make_result(0)

        mock_run.side_effect = side_effect

        findings = [
            {"file_path": "a.py", "line_number": 10, "body": "Issue 1"},
        ]
        result = asyncio.run(_post_pr_full_review("42", "Summary", findings, repo))
        assert result["success"] is False
        assert result["summary_posted"] is False
        assert result["threads_posted"] == 1
        assert len(result["errors"]) >= 1
        assert any("Summary" in e for e in result["errors"])

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_one_thread_fails(self, mock_run, tmp_path):
        """One thread fails → threads_posted=1, errors has 1 entry."""
        repo = _make_repo(tmp_path)

        api_call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal api_call_count
            cmd = " ".join(args)
            if "comment" in cmd and "--body" in cmd:
                return _make_result(0)  # summary OK
            if "repo" in cmd and "view" in cmd and "nameWithOwner" in cmd:
                return _make_result(0, "owner/repo")
            if "pr" in cmd and "view" in cmd and "headRefOid" in cmd:
                return _make_result(0, "abc123")
            # API POST for inline threads
            api_call_count += 1
            if api_call_count == 1:
                return _make_result(0)  # first thread OK
            return _make_result(1, stderr="error")  # second thread fails

        mock_run.side_effect = side_effect

        findings = [
            {"file_path": "a.py", "line_number": 10, "body": "Issue 1"},
            {"file_path": "b.py", "line_number": 20, "body": "Issue 2"},
        ]
        result = asyncio.run(_post_pr_full_review("42", "Summary", findings, repo))
        assert result["success"] is False
        assert result["summary_posted"] is True
        assert result["threads_posted"] == 1
        assert result["threads_total"] == 2
        assert len(result["errors"]) >= 1

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_empty_findings_list(self, mock_run, tmp_path):
        """Empty findings → only summary posted."""
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(0)

        result = asyncio.run(_post_pr_full_review("42", "Summary", [], repo))
        assert result["success"] is True
        assert result["summary_posted"] is True
        assert result["threads_posted"] == 0
        assert result["threads_total"] == 0

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_invalid_finding_missing_file_path(self, mock_run, tmp_path):
        """Finding missing file_path → error in threads list."""
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(0)  # summary succeeds

        findings = [
            {"file_path": "", "line_number": 10, "body": "Issue"},
        ]
        result = asyncio.run(_post_pr_full_review("42", "Summary", findings, repo))
        assert result["success"] is False
        assert any("missing file_path" in e for e in result["errors"])


# ── _create_github_pr Tests ───────────────────────────────


class TestCreateGithubPr:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success_url_parsed(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(
            0, stdout="Creating PR...\nhttps://github.com/owner/repo/pull/42\nDone"
        )

        result = asyncio.run(_create_github_pr(repo_root=repo))
        assert result["success"] is True
        assert result["pr_url"] == "https://github.com/owner/repo/pull/42"
        assert result["action"] == "pr_created"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_title_and_description_in_args(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(
            0, stdout="https://github.com/owner/repo/pull/1"
        )

        result = asyncio.run(_create_github_pr(
            repo_root=repo,
            title="Fix login bug",
            description="Fixes the auth timeout",
        ))
        assert result["success"] is True
        args = mock_run.call_args[0][0]
        assert "gh" in args
        assert "pr" in args
        assert "create" in args
        assert "--title" in args
        assert "Fix login bug" in args
        assert "--body" in args
        assert "Fixes the auth timeout" in args

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_draft_flag(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(
            0, stdout="https://github.com/owner/repo/pull/7"
        )

        result = asyncio.run(_create_github_pr(
            repo_root=repo,
            title="WIP feature",
            draft=True,
        ))
        assert result["success"] is True
        args = mock_run.call_args[0][0]
        assert "--draft" in args

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_no_fill_flag(self, mock_run, tmp_path):
        """gh doesn't have --fill — verify it's NOT in args."""
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(
            0, stdout="https://github.com/owner/repo/pull/1"
        )

        asyncio.run(_create_github_pr(repo_root=repo, title="Test"))
        args = mock_run.call_args[0][0]
        assert "--fill" not in args

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_base_head_branches(self, mock_run, tmp_path):
        """--base and --head are used for target/source branches."""
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(
            0, stdout="https://github.com/owner/repo/pull/8"
        )

        result = asyncio.run(_create_github_pr(
            repo_root=repo,
            title="Feature",
            target_branch="develop",
            source_branch="feature-x",
        ))
        assert result["success"] is True
        args = mock_run.call_args[0][0]
        assert "--base" in args
        assert "develop" in args
        assert "--head" in args
        assert "feature-x" in args

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_no_yes_flag_for_gh(self, mock_run, tmp_path):
        """gh doesn't use --yes (non-interactive when all args provided)."""
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(
            0, stdout="https://github.com/owner/repo/pull/1"
        )

        asyncio.run(_create_github_pr(repo_root=repo, title="Test"))
        args = mock_run.call_args[0][0]
        assert "--yes" not in args

    def test_invalid_repo_root(self, tmp_path):
        result = asyncio.run(_create_github_pr(repo_root="/nonexistent/path"))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_source_branch_rejected(self, tmp_path):
        repo = _make_repo(tmp_path)
        result = asyncio.run(_create_github_pr(
            repo_root=repo, source_branch="branch;rm -rf /"
        ))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_target_branch_rejected(self, tmp_path):
        repo = _make_repo(tmp_path)
        result = asyncio.run(_create_github_pr(
            repo_root=repo, target_branch="branch|evil"
        ))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_creation_failure(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(
            1, stderr="authentication required"
        )

        result = asyncio.run(_create_github_pr(repo_root=repo, title="Test"))
        assert result["success"] is False
        assert "authentication required" in result["error"]
        assert result["error_type"] == "pr_creation_failed"


# ── _fetch_pr_discussions Tests ───────────────────────────


class TestFetchPrDiscussions:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # gh repo view (repo_id)
                return _make_result(0, "owner/repo")
            return _make_result(0, SAMPLE_PR_DISCUSSIONS)  # comments API

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_pr_discussions("42", repo))
        assert result["success"] is True
        assert result["pr_id"] == "42"
        # 3 top-level comments + 1 reply = 3 top-level threads
        # (ids: 1001, 1003, 1004 are top-level; 1002 is a reply to 1001)
        assert result["total"] == 3

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_discussion_structure(self, mock_run, tmp_path):
        """Verify discussion structure has: id, type, file_path, line_number, body, author, replies."""
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, "owner/repo")
            return _make_result(0, SAMPLE_PR_DISCUSSIONS)

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_pr_discussions("42", repo))
        discussions = result["discussions"]

        # Verify required keys in each discussion
        required_keys = {"id", "type", "file_path", "line_number", "body", "author", "replies"}
        for disc in discussions:
            assert required_keys.issubset(set(disc.keys())), (
                f"Missing keys: {required_keys - set(disc.keys())}"
            )

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_inline_vs_general(self, mock_run, tmp_path):
        """Inline comments (with path) vs general comments (without path)."""
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, "owner/repo")
            return _make_result(0, SAMPLE_PR_DISCUSSIONS)

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_pr_discussions("42", repo))
        discussions = result["discussions"]

        inline = [d for d in discussions if d["type"] == "inline"]
        general = [d for d in discussions if d["type"] == "general"]

        assert len(inline) == 2
        assert inline[0]["file_path"] == "src/app.py"
        assert inline[0]["line_number"] == 42
        assert inline[1]["file_path"] == "src/utils.py"
        assert inline[1]["line_number"] == 10

        assert len(general) == 1
        assert general[0]["file_path"] is None
        assert "OmniForge" in general[0]["body"]

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_replies_grouped_to_parent(self, mock_run, tmp_path):
        """Replies are attached to their parent thread via in_reply_to_id."""
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, "owner/repo")
            return _make_result(0, SAMPLE_PR_DISCUSSIONS)

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_pr_discussions("42", repo))
        discussions = result["discussions"]

        # The first inline thread (id=1001) should have 1 reply
        parent = [d for d in discussions if d["id"] == "1001"]
        assert len(parent) == 1
        assert len(parent[0]["replies"]) == 1
        assert parent[0]["replies"][0]["author"] == "dev1"
        assert parent[0]["replies"][0]["body"] == "Will fix"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_empty_discussions(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, "owner/repo")
            return _make_result(0, "[]")

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_pr_discussions("42", repo))
        assert result["success"] is True
        assert result["discussions"] == []
        assert result["total"] == 0

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_api_failure(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, "owner/repo")
            return _make_result(1, stderr="500 Internal Server Error")

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_pr_discussions("42", repo))
        assert result["success"] is False
        assert result["error_type"] == "api_error"

    def test_invalid_pr_id(self, tmp_path):
        repo = _make_repo(tmp_path)
        result = asyncio.run(_fetch_pr_discussions("abc", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_repo_id_not_found(self, mock_run, tmp_path):
        """If repo_id can't be determined, returns repo_not_found."""
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(1, stderr="not found")

        result = asyncio.run(_fetch_pr_discussions("42", repo))
        assert result["success"] is False
        assert result["error_type"] == "repo_not_found"


# ── _reply_to_pr_comment Tests ────────────────────────────


class TestReplyToPrComment:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # gh repo view (repo_id)
                return _make_result(0, "owner/repo")
            return _make_result(0)  # POST reply

        mock_run.side_effect = side_effect

        result = asyncio.run(_reply_to_pr_comment(
            "42", "1001", "Fixed in latest commit.", repo
        ))
        assert result["success"] is True
        assert result["action"] == "reply_posted"
        assert result["comment_id"] == "1001"
        assert result["pr_id"] == "42"

        # Verify the POST call includes in_reply_to
        api_call = mock_run.call_args_list[-1][0][0]
        assert api_call[:2] == ["gh", "api"]
        assert "repos/owner/repo/pulls/42/comments" in api_call[2]
        assert "--method" in api_call
        assert "POST" in api_call
        fields = [api_call[i + 1] for i, a in enumerate(api_call) if a == "--field"]
        assert any("in_reply_to=1001" in f for f in fields)
        assert any("body=Fixed in latest commit." in f for f in fields)

    def test_empty_body(self, tmp_path):
        repo = _make_repo(tmp_path)
        result = asyncio.run(_reply_to_pr_comment("42", "1001", "", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_pr_id(self, tmp_path):
        repo = _make_repo(tmp_path)
        result = asyncio.run(_reply_to_pr_comment("abc", "1001", "Reply", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_api_failure(self, mock_run, tmp_path):
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, "owner/repo")
            return _make_result(1, stderr="403 Forbidden")

        mock_run.side_effect = side_effect

        result = asyncio.run(_reply_to_pr_comment(
            "42", "1001", "Reply text", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "post_failed"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_repo_id_not_found(self, mock_run, tmp_path):
        """If repo_id can't be determined, returns repo_not_found."""
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(1, stderr="not found")

        result = asyncio.run(_reply_to_pr_comment(
            "42", "1001", "Reply text", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "repo_not_found"
