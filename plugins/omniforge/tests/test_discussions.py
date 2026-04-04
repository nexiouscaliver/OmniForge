"""Tests for discussion tools: fetch, reply, and resolve."""

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


SAMPLE_MR_JSON = json.dumps({
    "iid": 136,
    "diff_refs": {
        "base_sha": "aaa111",
        "head_sha": "bbb222",
        "start_sha": "ccc333",
    },
})

SAMPLE_DISCUSSIONS_JSON = json.dumps([
    {
        "id": "disc-inline-1",
        "individual_note": False,
        "resolvable": True,
        "resolved": False,
        "notes": [
            {
                "id": 1001,
                "body": "**Important** \u2014 Missing null check",
                "author": {"username": "reviewer1"},
                "system": False,
                "created_at": "2026-03-26T01:30:00Z",
                "position": {
                    "new_path": "src/app.py",
                    "new_line": 42,
                    "position_type": "text"
                }
            },
            {
                "id": 1002,
                "body": "Will fix",
                "author": {"username": "dev1"},
                "system": False,
                "created_at": "2026-03-26T02:00:00Z"
            }
        ]
    },
    {
        "id": "disc-inline-2",
        "individual_note": False,
        "resolvable": True,
        "resolved": True,
        "notes": [
            {
                "id": 1003,
                "body": "**Minor** \u2014 Consider renaming",
                "author": {"username": "reviewer1"},
                "system": False,
                "created_at": "2026-03-26T01:30:00Z",
                "position": {
                    "new_path": "src/utils.py",
                    "new_line": 10,
                    "position_type": "text"
                }
            }
        ]
    },
    {
        "id": "disc-general-1",
        "individual_note": True,
        "resolvable": False,
        "resolved": False,
        "notes": [
            {
                "id": 1004,
                "body": "## OmniReview\n\n**Verdict:** APPROVE_WITH_FIXES",
                "author": {"username": "shahilkadia"},
                "system": False,
                "created_at": "2026-03-26T01:00:00Z"
            }
        ]
    },
    {
        "id": "disc-system",
        "individual_note": True,
        "resolvable": False,
        "resolved": False,
        "notes": [
            {
                "id": 1005,
                "body": "merged",
                "author": {"username": "system"},
                "system": True,
                "created_at": "2026-03-26T00:00:00Z"
            }
        ]
    }
])


# ── _fetch_mr_discussions Tests ──────────────────────────


class TestFetchMrDiscussions:
    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_success(self, mock_run, tmp_path):
        from omnireview_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # glab mr view (IID fetch)
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0, SAMPLE_DISCUSSIONS_JSON)  # paginated discussions

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_mr_discussions("136", repo))
        assert result["success"] is True
        assert result["total"] == 3  # system note discussion filtered out
        assert result["unresolved"] == 1
        assert result["resolved"] == 1
        assert result["mr_id"] == "136"

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_filters_system_notes(self, mock_run, tmp_path):
        """Discussion with only system notes should be completely excluded."""
        from omnireview_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)

        system_only = json.dumps([
            {
                "id": "disc-system-only",
                "individual_note": True,
                "resolvable": False,
                "resolved": False,
                "notes": [
                    {
                        "id": 2001,
                        "body": "changed the description",
                        "author": {"username": "system"},
                        "system": True,
                        "created_at": "2026-03-26T00:00:00Z"
                    }
                ]
            }
        ])

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0, system_only)

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_mr_discussions("136", repo))
        assert result["success"] is True
        assert result["total"] == 0
        assert result["discussions"] == []

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_inline_vs_general(self, mock_run, tmp_path):
        """Inline discussions have position data, general ones do not."""
        from omnireview_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0, SAMPLE_DISCUSSIONS_JSON)

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_mr_discussions("136", repo))
        discussions = result["discussions"]

        # First two are inline (have position data)
        inline_discs = [d for d in discussions if d["type"] == "inline"]
        assert len(inline_discs) == 2
        assert inline_discs[0]["file_path"] == "src/app.py"
        assert inline_discs[0]["line_number"] == 42
        assert inline_discs[1]["file_path"] == "src/utils.py"
        assert inline_discs[1]["line_number"] == 10

        # Third is general (no position data)
        general_discs = [d for d in discussions if d["type"] == "general"]
        assert len(general_discs) == 1
        assert general_discs[0]["file_path"] is None
        assert general_discs[0]["line_number"] is None

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_skips_omnireview_summary(self, mock_run, tmp_path):
        """General discussion starting with '## OmniReview' appears as type='general' with resolvable=False."""
        from omnireview_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0, SAMPLE_DISCUSSIONS_JSON)

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_mr_discussions("136", repo))
        omni_disc = [d for d in result["discussions"] if "OmniReview" in d["body"]]
        assert len(omni_disc) == 1
        assert omni_disc[0]["type"] == "general"
        assert omni_disc[0]["resolvable"] is False

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_empty_discussions(self, mock_run, tmp_path):
        from omnireview_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0, "[]")

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_mr_discussions("136", repo))
        assert result["success"] is True
        assert result["discussions"] == []
        assert result["total"] == 0
        assert result["unresolved"] == 0
        assert result["resolved"] == 0

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_api_failure(self, mock_run, tmp_path):
        """IID fetch OK but discussions API call fails."""
        from omnireview_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(1, stderr="500 Internal Server Error")

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_mr_discussions("136", repo))
        assert result["success"] is False
        assert result["error_type"] == "api_error"

    def test_invalid_mr_id(self, tmp_path):
        from omnireview_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)

        result = asyncio.run(_fetch_mr_discussions("abc", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"


# ── _reply_to_discussion Tests ───────────────────────────


class TestReplyToDiscussion:
    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_reply_success(self, mock_run, tmp_path):
        from omnireview_mcp_server import _reply_to_discussion
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # glab mr view (IID fetch)
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0)  # POST reply

        mock_run.side_effect = side_effect

        result = asyncio.run(_reply_to_discussion(
            "136", "disc-inline-1", "Fixed in latest commit.", repo
        ))
        assert result["success"] is True
        assert result["action"] == "reply_posted"
        assert result["discussion_id"] == "disc-inline-1"
        assert result["mr_id"] == "136"

        # Verify the POST call
        api_call = mock_run.call_args_list[1][0][0]
        assert "discussions/disc-inline-1/notes" in api_call[2]
        assert "--method" in api_call
        assert "POST" in api_call

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_reply_failure(self, mock_run, tmp_path):
        """POST returns 403."""
        from omnireview_mcp_server import _reply_to_discussion
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(1, stderr="403 Forbidden")

        mock_run.side_effect = side_effect

        result = asyncio.run(_reply_to_discussion(
            "136", "disc-inline-1", "Reply text", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "post_failed"

    def test_reply_empty_body(self, tmp_path):
        from omnireview_mcp_server import _reply_to_discussion
        repo = _make_repo(tmp_path)

        result = asyncio.run(_reply_to_discussion(
            "136", "disc-inline-1", "", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    def test_reply_invalid_mr_id(self, tmp_path):
        from omnireview_mcp_server import _reply_to_discussion
        repo = _make_repo(tmp_path)

        result = asyncio.run(_reply_to_discussion(
            "abc", "disc-inline-1", "Reply text", repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"


# ── _resolve_discussion Tests ────────────────────────────


class TestResolveDiscussion:
    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_resolve_success(self, mock_run, tmp_path):
        from omnireview_mcp_server import _resolve_discussion
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # glab mr view (IID fetch)
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0)  # PUT resolve

        mock_run.side_effect = side_effect

        result = asyncio.run(_resolve_discussion(
            "136", "disc-inline-1", True, repo
        ))
        assert result["success"] is True
        assert result["resolved"] is True
        assert result["action"] == "discussion_resolved"
        assert result["discussion_id"] == "disc-inline-1"
        assert result["mr_id"] == "136"

        # Verify the PUT call
        api_call = mock_run.call_args_list[1][0][0]
        assert "discussions/disc-inline-1" in api_call[2]
        assert "--method" in api_call
        assert "PUT" in api_call
        assert any("resolved=true" in a for a in api_call)

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_unresolve_success(self, mock_run, tmp_path):
        from omnireview_mcp_server import _resolve_discussion
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0)

        mock_run.side_effect = side_effect

        result = asyncio.run(_resolve_discussion(
            "136", "disc-inline-1", False, repo
        ))
        assert result["success"] is True
        assert result["resolved"] is False
        assert result["action"] == "discussion_unresolved"

        # Verify resolved=false in the PUT call
        api_call = mock_run.call_args_list[1][0][0]
        assert any("resolved=false" in a for a in api_call)

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_resolve_failure(self, mock_run, tmp_path):
        """PUT returns 404."""
        from omnireview_mcp_server import _resolve_discussion
        repo = _make_repo(tmp_path)

        call_count = 0
        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(1, stderr="404 Not Found")

        mock_run.side_effect = side_effect

        result = asyncio.run(_resolve_discussion(
            "136", "disc-inline-1", True, repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "resolve_failed"

    def test_resolve_invalid_mr_id(self, tmp_path):
        from omnireview_mcp_server import _resolve_discussion
        repo = _make_repo(tmp_path)

        result = asyncio.run(_resolve_discussion(
            "abc", "disc-inline-1", True, repo
        ))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"


# ── TestDiffNoteTypeFallback Tests ───────────────────────


class TestDiffNoteTypeFallback:
    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_diffnote_without_position_classified_as_inline(self, mock_run, tmp_path):
        """DiffNote with position=None should be classified as inline via type fallback."""
        from omnireview_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)

        discussions_json = json.dumps([
            {
                "id": "disc-diffnote-no-pos",
                "individual_note": False,
                "resolvable": True,
                "resolved": False,
                "notes": [
                    {
                        "id": 2001,
                        "type": "DiffNote",
                        "body": "Missing null check here",
                        "author": {"username": "reviewer1"},
                        "system": False,
                        "created_at": "2026-03-28T10:00:00Z",
                        "position": None,
                    }
                ],
            }
        ])

        call_count = 0

        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0, discussions_json)

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_mr_discussions("136", repo))
        assert result["success"] is True
        assert result["total"] == 1
        disc = result["discussions"][0]
        assert disc["type"] == "inline"
        assert disc["file_path"] is None
        assert disc["line_number"] is None

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_diffnote_with_position_uses_position_data(self, mock_run, tmp_path):
        """DiffNote with full position data should be inline with correct file/line."""
        from omnireview_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)

        discussions_json = json.dumps([
            {
                "id": "disc-diffnote-with-pos",
                "individual_note": False,
                "resolvable": True,
                "resolved": False,
                "notes": [
                    {
                        "id": 2002,
                        "type": "DiffNote",
                        "body": "Rename this variable",
                        "author": {"username": "reviewer2"},
                        "system": False,
                        "created_at": "2026-03-28T11:00:00Z",
                        "position": {
                            "new_path": "src/models.py",
                            "new_line": 77,
                            "position_type": "text",
                        },
                    }
                ],
            }
        ])

        call_count = 0

        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0, discussions_json)

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_mr_discussions("136", repo))
        assert result["success"] is True
        assert result["total"] == 1
        disc = result["discussions"][0]
        assert disc["type"] == "inline"
        assert disc["file_path"] == "src/models.py"
        assert disc["line_number"] == 77

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_regular_note_without_position_is_general(self, mock_run, tmp_path):
        """Note with type=null and no position should be classified as general."""
        from omnireview_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)

        discussions_json = json.dumps([
            {
                "id": "disc-regular-note",
                "individual_note": True,
                "resolvable": False,
                "resolved": False,
                "notes": [
                    {
                        "id": 2003,
                        "type": None,
                        "body": "Overall looks good to me",
                        "author": {"username": "reviewer3"},
                        "system": False,
                        "created_at": "2026-03-28T12:00:00Z",
                    }
                ],
            }
        ])

        call_count = 0

        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0, discussions_json)

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_mr_discussions("136", repo))
        assert result["success"] is True
        assert result["total"] == 1
        disc = result["discussions"][0]
        assert disc["type"] == "general"
        assert disc["file_path"] is None
        assert disc["line_number"] is None

    @patch("omnireview_mcp_server.run_exec", new_callable=AsyncMock)
    def test_diffnote_missing_position_key_entirely(self, mock_run, tmp_path):
        """DiffNote with no position key at all should be classified as inline."""
        from omnireview_mcp_server import _fetch_mr_discussions
        repo = _make_repo(tmp_path)

        discussions_json = json.dumps([
            {
                "id": "disc-diffnote-no-key",
                "individual_note": False,
                "resolvable": True,
                "resolved": False,
                "notes": [
                    {
                        "id": 2004,
                        "type": "DiffNote",
                        "body": "Add docstring",
                        "author": {"username": "reviewer4"},
                        "system": False,
                        "created_at": "2026-03-28T13:00:00Z",
                        # No "position" key at all
                    }
                ],
            }
        ])

        call_count = 0

        def side_effect(args, cwd=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(0, SAMPLE_MR_JSON)
            return _make_result(0, discussions_json)

        mock_run.side_effect = side_effect

        result = asyncio.run(_fetch_mr_discussions("136", repo))
        assert result["success"] is True
        assert result["total"] == 1
        disc = result["discussions"][0]
        assert disc["type"] == "inline"
        assert disc["file_path"] is None
        assert disc["line_number"] is None
