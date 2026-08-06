"""Tests for the approve_mr tool (bot-token vs current-user approval)."""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, patch

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


# Event-loop hygiene across the mixed asyncio.run / get_event_loop suite is
# handled by the autouse fixture in conftest.py; tests here just use asyncio.run.


SAMPLE_MR_JSON = json.dumps({
    "iid": 136,
    "diff_refs": {
        "base_sha": "aaa111",
        "head_sha": "deadbeef",
        "start_sha": "ccc333",
    },
})


class TestApproveMr:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_approve_as_bot_when_token_set(self, mock_run, tmp_path):
        """When OMNICHECK_BOT_TOKEN is set, the approve call runs as the bot."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60, env=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # glab mr view (IID + head_sha fetch)
                assert env is None, "IID fetch must inherit the normal environment"
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0, stdout="{}")  # POST approve

        mock_run.side_effect = side_effect

        with patch.dict(os.environ, {"OMNICHECK_BOT_TOKEN": "glpat-bot-secret"}, clear=False):
            result = asyncio.run(_approve_mr("136", repo))

        assert result["success"] is True
        assert result["approver"] == "bot"
        assert result["sha"] == "deadbeef"  # head_sha pinned
        assert result["action"] == "mr_approved"

        # The approve call must have carried the bot token via GITLAB_TOKEN env
        approve_call_kwargs = mock_run.call_args_list[1].kwargs
        approve_env = approve_call_kwargs["env"]
        assert approve_env["GITLAB_TOKEN"] == "glpat-bot-secret"
        # And the rest of the environment is preserved
        assert "PATH" in approve_env

        # The approve command targeted the approve endpoint with sha pinned
        approve_args = mock_run.call_args_list[1][0][0]
        assert "approve" in approve_args[2]
        assert "--method" in approve_args
        assert "POST" in approve_args
        assert any("sha=deadbeef" in a for a in approve_args)

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_approve_as_current_user_when_no_token(self, mock_run, tmp_path):
        """Without the bot token, approval runs as the current glab user (no env override)."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60, env=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            assert env is None, "no bot token => env must be None (inherit)"
            return _make_result(0, stdout="{}")

        mock_run.side_effect = side_effect

        with patch.dict(os.environ, {}, clear=True):
            # OMNICHECK_BOT_TOKEN absent
            os.environ.pop("OMNICHECK_BOT_TOKEN", None)
            result = asyncio.run(_approve_mr("136", repo))

        assert result["success"] is True
        assert result["approver"] == "current_user"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_custom_sha_pinned(self, mock_run, tmp_path):
        """An explicit sha overrides the resolved head_sha."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60, env=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0, stdout="{}")

        mock_run.side_effect = side_effect

        result = asyncio.run(_approve_mr("136", repo, sha="cafef00d"))
        assert result["success"] is True
        assert result["sha"] == "cafef00d"
        approve_args = mock_run.call_args_list[1][0][0]
        assert any("sha=cafef00d" in a for a in approve_args)
        assert not any("sha=deadbeef" in a for a in approve_args)

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_resolve_head_sha_by_default(self, mock_run, tmp_path):
        """Empty sha resolves to diff_refs head_sha."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)

        mock_run.side_effect = [
            _make_result(0, SAMPLE_MR_JSON),
            _make_result(0, stdout="{}"),
        ]
        result = asyncio.run(_approve_mr("136", repo))
        assert result["sha"] == "deadbeef"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_approve_failure_403(self, mock_run, tmp_path):
        """A 403 (blocked approver) returns approve_failed."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)

        mock_run.side_effect = [
            _make_result(0, SAMPLE_MR_JSON),
            _make_result(1, stderr="403 Forbidden: not allowed to approve"),
        ]
        result = asyncio.run(_approve_mr("136", repo))
        assert result["success"] is False
        assert result["error_type"] == "approve_failed"
        assert "403" in result["error"]

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_mr_not_found(self, mock_run, tmp_path):
        """If the IID fetch fails, approval is not attempted."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)

        mock_run.side_effect = [_make_result(1, stderr="404 Not Found")]
        result = asyncio.run(_approve_mr("136", repo))
        assert result["success"] is False
        assert result["error_type"] == "mr_not_found"
        assert mock_run.call_count == 1  # only the IID fetch, no approve POST

    def test_invalid_mr_id(self, tmp_path):
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)
        result = asyncio.run(_approve_mr("abc", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_invalid_sha(self, tmp_path):
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)
        result = asyncio.run(_approve_mr("136", repo, sha="not-a-sha!"))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_strips_bang_prefix(self, mock_run, tmp_path):
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, SAMPLE_MR_JSON),
            _make_result(0, stdout="{}"),
        ]
        result = asyncio.run(_approve_mr("!136", repo))
        assert result["success"] is True
        assert result["mr_id"] == "136"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_refuses_unpinned_when_no_head_sha(self, mock_run, tmp_path):
        """If diff_refs has no head_sha and none was passed, refuse rather than approve unpinned."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)

        no_head_sha = json.dumps({
            "iid": 136,
            "diff_refs": {"base_sha": "aaa111", "head_sha": "", "start_sha": "ccc333"},
        })
        mock_run.side_effect = [_make_result(0, no_head_sha)]

        result = asyncio.run(_approve_mr("136", repo))
        assert result["success"] is False
        assert result["error_type"] == "no_head_sha"
        # The approve endpoint was never hit — only the IID fetch ran.
        assert mock_run.call_count == 1

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_explicit_sha_allows_approval_without_head_sha(self, mock_run, tmp_path):
        """An explicit sha bypasses the no-head-sha guard (caller took responsibility)."""
        from omniforge_mcp_server import _approve_mr
        repo = _make_repo(tmp_path)

        no_head_sha = json.dumps({
            "iid": 136,
            "diff_refs": {"base_sha": "aaa111", "head_sha": "", "start_sha": "ccc333"},
        })
        mock_run.side_effect = [
            _make_result(0, no_head_sha),
            _make_result(0, stdout="{}"),
        ]

        result = asyncio.run(_approve_mr("136", repo, sha="cafef00d"))
        assert result["success"] is True
        assert result["sha"] == "cafef00d"

