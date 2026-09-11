"""S1 - MCP posting helpers for the screenshot ask-author flow.

Tests for the three new helpers in tools/omniforge_mcp_server.py, all with
MOCKED transport (run_exec) - nothing here touches the network:

- _update_mr_labels: ONE PUT with add_labels/remove_labels raw-fields
  (auto-create semantics, no GET-merge-PUT race, no label pre-creation).
- _post_mr_note: API note posting that RETURNS the discussion_id (the
  durable thread identity reply detection matches against).
- _upload_project_file / build_multipart_upload_body: multipart
  POST /projects/:id/uploads via glab api --input (installed glab 1.6x has
  no --form flag; the multipart body is built here and sent raw).
"""

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


DIFF_REFS_JSON = json.dumps({
    "iid": 30,
    "diff_refs": {"base_sha": "aaa", "head_sha": "bbb", "start_sha": "ccc"},
    "labels": ["omniforge-ui"],
    "state": "opened",
})

UPDATED_MR_JSON = json.dumps({
    "iid": 30,
    "labels": ["omniforge-ui", "omniforge-screenshot-requested"],
})

NOTE_JSON = json.dumps({
    "id": 981,
    "body": "please post a screenshot",
    "discussion_id": "abc-def-123",
})

UPLOAD_JSON = json.dumps({
    "markdown": "![img](/uploads/hash/shot.png)",
    "url": "/uploads/hash/shot.png",
    "full_url": "https://gitlab.com/g/p/-/raw/hash/shot.png",
})


# -- _update_mr_labels ----------------------------------------


class TestSUpdateMrLabels:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_one_put_with_add_and_remove_no_get(self, mock_run, tmp_path):
        from omniforge_mcp_server import _update_mr_labels
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, DIFF_REFS_JSON),   # glab mr view -> iid
            _make_result(0, UPDATED_MR_JSON),  # glab api PUT
        ]

        result = asyncio.run(_update_mr_labels(
            "30", add_labels="omniforge-ui,omniforge-screenshot-requested",
            remove_labels="old-label", repo_root=repo))

        assert result["success"] is True
        assert result["action"] == "labels_updated"
        assert result["labels"] == ["omniforge-ui",
                                    "omniforge-screenshot-requested"]
        # exactly two subprocess calls: mr view + ONE put (no GET-merge-PUT)
        assert mock_run.call_count == 2
        put_args = mock_run.call_args_list[1][0][0]
        assert put_args[:3] == ["glab", "api",
                                "projects/:fullpath/merge_requests/30"]
        assert "--method" in put_args and "PUT" in put_args
        assert "add_labels=omniforge-ui,omniforge-screenshot-requested" in put_args
        assert "remove_labels=old-label" in put_args

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_add_only_omits_remove_field(self, mock_run, tmp_path):
        from omniforge_mcp_server import _update_mr_labels
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, DIFF_REFS_JSON),
            _make_result(0, UPDATED_MR_JSON),
        ]

        result = asyncio.run(_update_mr_labels(
            "30", add_labels="omniforge-ui", repo_root=repo))
        assert result["success"] is True
        put_args = mock_run.call_args_list[1][0][0]
        assert "add_labels=omniforge-ui" in put_args
        assert not any(a.startswith("remove_labels=") for a in put_args)

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_remove_only_omits_add_field(self, mock_run, tmp_path):
        from omniforge_mcp_server import _update_mr_labels
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, DIFF_REFS_JSON),
            _make_result(0, UPDATED_MR_JSON),
        ]

        result = asyncio.run(_update_mr_labels(
            "30", remove_labels="omniforge-screenshot-requested",
            repo_root=repo))
        assert result["success"] is True
        put_args = mock_run.call_args_list[1][0][0]
        assert "remove_labels=omniforge-screenshot-requested" in put_args
        assert not any(a.startswith("add_labels=") for a in put_args)

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_no_labels_given_is_validation_error(self, mock_run, tmp_path):
        from omniforge_mcp_server import _update_mr_labels
        repo = _make_repo(tmp_path)

        result = asyncio.run(_update_mr_labels("30", repo_root=repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"
        assert mock_run.call_count == 0  # refused before any subprocess

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_bad_mr_id_is_validation_error(self, mock_run, tmp_path):
        from omniforge_mcp_server import _update_mr_labels
        repo = _make_repo(tmp_path)

        result = asyncio.run(_update_mr_labels(
            "abc", add_labels="x", repo_root=repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_control_chars_in_labels_rejected(self, mock_run, tmp_path):
        from omniforge_mcp_server import _update_mr_labels
        repo = _make_repo(tmp_path)

        result = asyncio.run(_update_mr_labels(
            "30", add_labels="bad\nlabel", repo_root=repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"
        assert mock_run.call_count == 0

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_glab_failure(self, mock_run, tmp_path):
        from omniforge_mcp_server import _update_mr_labels
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, DIFF_REFS_JSON),
            _make_result(1, stderr="403"),
        ]

        result = asyncio.run(_update_mr_labels(
            "30", add_labels="omniforge-ui", repo_root=repo))
        assert result["success"] is False
        assert result["error_type"] == "label_update_failed"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_unparseable_response(self, mock_run, tmp_path):
        from omniforge_mcp_server import _update_mr_labels
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, DIFF_REFS_JSON),
            _make_result(0, "not json"),
        ]

        result = asyncio.run(_update_mr_labels(
            "30", add_labels="omniforge-ui", repo_root=repo))
        assert result["success"] is False
        assert result["error_type"] == "parse_error"


# -- _post_mr_note ---------------------------------------------


class TestSPostMrNote:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_success_returns_discussion_id(self, mock_run, tmp_path):
        from omniforge_mcp_server import _post_mr_note
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, DIFF_REFS_JSON),  # mr view
            _make_result(0, NOTE_JSON),       # api POST note
        ]

        result = asyncio.run(_post_mr_note(
            "30", "UI render request - please reply with a screenshot", repo))

        assert result["success"] is True
        assert result["note_id"] == 981
        assert result["discussion_id"] == "abc-def-123"
        assert result["action"] == "note_posted"
        post_args = mock_run.call_args_list[1][0][0]
        assert post_args[:3] == ["glab", "api",
                                 "projects/:fullpath/merge_requests/30/notes"]
        assert "--method" in post_args and "POST" in post_args
        assert "body=UI render request - please reply with a screenshot" in post_args

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_empty_body_validation_error(self, mock_run, tmp_path):
        from omniforge_mcp_server import _post_mr_note
        repo = _make_repo(tmp_path)

        result = asyncio.run(_post_mr_note("30", "  ", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_glab_failure(self, mock_run, tmp_path):
        from omniforge_mcp_server import _post_mr_note
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, DIFF_REFS_JSON),
            _make_result(1, stderr="429 Too Many Requests"),
        ]

        result = asyncio.run(_post_mr_note("30", "body", repo))
        assert result["success"] is False
        assert result["error_type"] == "post_failed"


# -- multipart builder + _upload_project_file ------------------


class TestSBuildMultipartBody:
    def test_s1_builder_shape(self, tmp_path):
        from omniforge_mcp_server import build_multipart_upload_body
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG fake bytes")

        body = build_multipart_upload_body(str(img))

        assert isinstance(body, bytes)
        assert body.startswith(b"--")
        assert b'Content-Disposition: form-data; name="file"; filename="shot.png"' in body
        assert b"\x89PNG fake bytes" in body
        # closed with a terminating boundary
        assert body.rstrip().endswith(b"--")

    def test_s1_builder_content_type(self, tmp_path):
        from omniforge_mcp_server import build_multipart_upload_body
        img = tmp_path / "shot.png"
        img.write_bytes(b"png")

        body = build_multipart_upload_body(str(img))
        boundary = body.split(b"\r\n")[0][2:].decode()

        from omniforge_mcp_server import multipart_content_type
        assert multipart_content_type() == (
            f"multipart/form-data; boundary={boundary}")

    def test_s1_builder_guesses_png_content_type(self, tmp_path):
        from omniforge_mcp_server import build_multipart_upload_body
        img = tmp_path / "shot.png"
        img.write_bytes(b"png")
        body = build_multipart_upload_body(str(img))
        assert b"Content-Type: image/png" in body


class TestSUploadProjectFile:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_success_sends_built_body_via_input(self, mock_run, tmp_path):
        from omniforge_mcp_server import (
            _upload_project_file, build_multipart_upload_body,
            multipart_content_type,
        )
        repo = _make_repo(tmp_path)
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG upload me")
        expected = build_multipart_upload_body(str(img))
        mock_run.return_value = _make_result(0, UPLOAD_JSON)

        result = asyncio.run(_upload_project_file(str(img), repo))

        assert result["success"] is True
        assert result["markdown"] == "![img](/uploads/hash/shot.png)"
        assert result["url"] == "/uploads/hash/shot.png"
        assert result["action"] == "file_uploaded"

        args = mock_run.call_args_list[0][0][0]
        assert args[:3] == ["glab", "api", "projects/:fullpath/uploads"]
        assert "--method" in args and "POST" in args
        assert "--input" in args
        assert multipart_content_type() in args
        assert "-H" in args
        # temp body file cleaned up after the call
        input_path = args[args.index("--input") + 1]
        assert not os.path.exists(input_path)

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_request_shape_and_body_bytes(self, mock_run, tmp_path):
        from omniforge_mcp_server import (
            _upload_project_file, build_multipart_upload_body,
            multipart_content_type,
        )
        repo = _make_repo(tmp_path)
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG payload")
        expected = build_multipart_upload_body(str(img))

        captured = {}

        async def fake_run(args, cwd=None, timeout=60, env=None):
            captured["args"] = args
            input_path = args[args.index("--input") + 1]
            with open(input_path, "rb") as fh:
                captured["body"] = fh.read()
            captured["header"] = args[args.index("-H") + 1]
            return _make_result(0, UPLOAD_JSON)

        mock_run.side_effect = fake_run

        result = asyncio.run(_upload_project_file(str(img), repo))
        assert result["success"] is True
        assert captured["body"] == expected
        assert captured["header"] == multipart_content_type()

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_missing_file_validation_error(self, mock_run, tmp_path):
        from omniforge_mcp_server import _upload_project_file
        repo = _make_repo(tmp_path)

        result = asyncio.run(_upload_project_file(
            str(tmp_path / "nope.png"), repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"
        assert mock_run.call_count == 0

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_oversize_file_rejected(self, mock_run, tmp_path):
        from omniforge_mcp_server import MAX_UPLOAD_BYTES, _upload_project_file
        repo = _make_repo(tmp_path)
        img = tmp_path / "big.png"
        img.write_bytes(b"x" * 16)
        # monkeypatch the limit down instead of writing 100 MiB
        import omniforge_mcp_server
        original = omniforge_mcp_server.MAX_UPLOAD_BYTES
        omniforge_mcp_server.MAX_UPLOAD_BYTES = 8
        try:
            result = asyncio.run(_upload_project_file(str(img), repo))
        finally:
            omniforge_mcp_server.MAX_UPLOAD_BYTES = original
        assert result["success"] is False
        assert result["error_type"] == "validation_error"
        assert mock_run.call_count == 0
        assert original == 100 * 1024 * 1024  # platform limit pinned

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_glab_failure(self, mock_run, tmp_path):
        from omniforge_mcp_server import _upload_project_file
        repo = _make_repo(tmp_path)
        img = tmp_path / "shot.png"
        img.write_bytes(b"png")
        mock_run.return_value = _make_result(1, stderr="413 too large")

        result = asyncio.run(_upload_project_file(str(img), repo))
        assert result["success"] is False
        assert result["error_type"] == "upload_failed"

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_unparseable_response(self, mock_run, tmp_path):
        from omniforge_mcp_server import _upload_project_file
        repo = _make_repo(tmp_path)
        img = tmp_path / "shot.png"
        img.write_bytes(b"png")
        mock_run.return_value = _make_result(0, "<html>nope</html>")

        result = asyncio.run(_upload_project_file(str(img), repo))
        assert result["success"] is False
        assert result["error_type"] == "parse_error"


# -- registration wiring ---------------------------------------


class TestSToolRegistration:
    def test_s1_three_new_tools_registered(self):
        from omniforge_mcp_server import mcp_server
        tools = asyncio.run(mcp_server.list_tools())
        names = {t.name for t in tools}
        assert {"update_mr_labels", "post_mr_note",
                "upload_project_file"} <= names
