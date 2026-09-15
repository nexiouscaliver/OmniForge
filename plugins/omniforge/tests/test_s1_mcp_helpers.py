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
        # glab api -H requires "Key: value" form: a bare header VALUE is a
        # local usage error (glab exits 1, "requires a value separated by
        # ':'") and the boundary never rides the request. Pinned verbatim.
        assert f"Content-Type: {multipart_content_type()}" in args
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
        assert captured["header"] == (
            f"Content-Type: {multipart_content_type()}")

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


# -- Review round 1: seam normalization + hardening ------------


class TestSLabelSeamNormalization:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_accepts_plan_lists_verbatim(self, mock_run, tmp_path):
        # decision plans emit labels_add/labels_remove as LISTS; the helper
        # must take them verbatim (joined), never TypeError
        from omniforge_mcp_server import _update_mr_labels
        repo = _make_repo(tmp_path)
        mock_run.side_effect = [
            _make_result(0, DIFF_REFS_JSON),
            _make_result(0, UPDATED_MR_JSON),
        ]
        result = asyncio.run(_update_mr_labels(
            "30",
            add_labels=["omniforge-ui", "omniforge-screenshot-requested"],
            remove_labels=["old-label"], repo_root=repo))
        assert result["success"] is True
        put_args = mock_run.call_args_list[1][0][0]
        assert "add_labels=omniforge-ui,omniforge-screenshot-requested" in put_args
        assert "remove_labels=old-label" in put_args

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_rejects_non_string_label_items(self, mock_run, tmp_path):
        from omniforge_mcp_server import _update_mr_labels
        repo = _make_repo(tmp_path)
        result = asyncio.run(_update_mr_labels(
            "30", add_labels=[42], repo_root=repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"
        assert mock_run.call_count == 0


class TestSUploadHardening:
    def test_s1_golden_body_bytes(self, tmp_path):
        # byte-exact contract, not circular: exact literal for a known file
        from omniforge_mcp_server import build_multipart_upload_body
        img = tmp_path / "t.png"
        img.write_bytes(b"png")
        expected = (
            b"--omniforge-upload-3f2a8c1d\r\n"
            b'Content-Disposition: form-data; name="file"; filename="t.png"\r\n'
            b"Content-Type: image/png\r\n"
            b"\r\n"
            b"png\r\n"
            b"--omniforge-upload-3f2a8c1d--\r\n"
        )
        assert build_multipart_upload_body(str(img)) == expected

    def test_s1_rejects_quote_in_filename(self, tmp_path):
        from omniforge_mcp_server import build_multipart_upload_body
        bad = tmp_path / 'weird";x=\r\n.png'
        bad.write_bytes(b"png")
        with pytest.raises(ValueError):
            build_multipart_upload_body(str(bad))

    def test_s1_rejects_control_chars_in_filename(self, tmp_path):
        from omniforge_mcp_server import build_multipart_upload_body
        bad = tmp_path / "bad\nname.png"
        bad.write_bytes(b"png")
        with pytest.raises(ValueError):
            build_multipart_upload_body(str(bad))

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_relative_path_rejected(self, mock_run, tmp_path):
        from omniforge_mcp_server import _upload_project_file
        repo = _make_repo(tmp_path)
        img = tmp_path / "shot.png"
        img.write_bytes(b"png")
        result = asyncio.run(_upload_project_file("shot.png", repo))
        assert result["success"] is False
        assert result["error_type"] == "validation_error"
        assert mock_run.call_count == 0


# -- Review round 1: mocked end-to-end sequence ----------------


class TestSSequence:
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s1_full_ask_reply_clear_sequence(self, mock_run, tmp_path):
        """The no-network dry run: one MR walks ask -> author image reply
        -> label clear, with every value crossing the seam asserted."""
        import omni_ui_screenshot as flow
        from omniforge_mcp_server import _post_mr_note, _update_mr_labels

        repo = _make_repo(tmp_path)

        # 1. detection: frontend MR
        detection = flow.classify_frontend_change([
            "src/admin/Console.tsx", "app/main.py", "README.md"])
        assert detection["level"] == "strong"

        # 2. review round 1 decides the ask
        state = flow.new_state()
        plan = flow.decide_review_ask(state, 1, detection)
        assert plan["action"] == "ask"

        # 3. the ask posts as a note; its discussion id is recorded
        mock_run.side_effect = [
            _make_result(0, DIFF_REFS_JSON),
            _make_result(0, NOTE_JSON),
        ]
        note = asyncio.run(_post_mr_note(
            "30", "UI change detected - please reply with a screenshot", repo))
        assert note["success"] is True
        state = flow.record_ask(state, 1, note["discussion_id"])
        assert state["discussion_id"] == "abc-def-123"

        # 4. labels cross the seam as plan lists, verbatim
        mock_run.reset_mock()
        mock_run.side_effect = [
            _make_result(0, DIFF_REFS_JSON),
            _make_result(0, UPDATED_MR_JSON),
        ]
        labeled = asyncio.run(_update_mr_labels(
            "30", add_labels=plan["labels_add"],
            remove_labels=plan["labels_remove"], repo_root=repo))
        assert labeled["success"] is True

        # 5. author replies on the recorded thread with an uploaded image
        reply_body = "here: ![console](/uploads/h/console.png)"
        assert flow.detect_image_reply(
            state["discussion_id"], "abc-def-123", reply_body) is True

        # 6. the reply is accepted; the requested label clears
        accepted = flow.on_image_reply(state)
        assert accepted["action"] == "image_accepted"
        mock_run.reset_mock()
        mock_run.side_effect = [
            _make_result(0, DIFF_REFS_JSON),
            _make_result(0, UPDATED_MR_JSON),
        ]
        cleared = asyncio.run(_update_mr_labels(
            "30", add_labels=accepted["labels_add"],
            remove_labels=accepted["labels_remove"], repo_root=repo))
        assert cleared["success"] is True
        put_args = mock_run.call_args_list[1][0][0]
        assert "remove_labels=omniforge-screenshot-requested" in put_args

        # 7. a later frontend push while posted re-arms exactly once/round
        state = accepted["state"]
        rearm = flow.decide_push_reask(state, 1, detection)
        assert rearm["action"] == "reask"
        assert rearm["state"]["discussion_id"] == "abc-def-123"
        assert flow.decide_push_reask(rearm["state"], 1, detection)["action"] == "none"


# -- SC-8: upload boundary --selftest (explicit invoke only) -----


class TestSSelftest:
    """The --selftest CLI verdicts (AC-8.1-8.6), transport mocked like
    TestSUploadProjectFile plus one real-__main__ subprocess run with a
    stub glab shadowed on PATH (hermetic: the stub never reaches the
    network)."""

    @patch("omniforge_mcp_server.mcp_server")
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s8_ok_exit_0_one_line(self, mock_run, mock_server, tmp_path,
                                   capsys, monkeypatch):
        import tempfile
        from omniforge_mcp_server import main
        tdir = tmp_path / "tempdir"
        tdir.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(tdir))
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(0, UPLOAD_JSON)

        code = main(["--selftest", "--selftest-project", repo])

        assert code == 0
        # EXACTLY one stdout line, the verdict
        assert capsys.readouterr().out.splitlines() == ["BOUNDARY VERDICT: OK"]
        mock_server.run.assert_not_called()  # flag path never runs the server
        assert mock_run.call_count == 1  # one glab upload call
        # both tempfiles cleaned up: the selftest PNG and the multipart body
        assert list(tdir.iterdir()) == []

    @patch("omniforge_mcp_server.mcp_server")
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s8_empty_markdown_exit_4_stripped(self, mock_run, mock_server,
                                               tmp_path, capsys):
        from omniforge_mcp_server import main
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(0, json.dumps({}))

        code = main(["--selftest", "--selftest-project", repo])

        assert code == 4
        assert capsys.readouterr().out.splitlines() == [
            "BOUNDARY VERDICT: STRIPPED"]
        mock_server.run.assert_not_called()

    @patch("omniforge_mcp_server.mcp_server")
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s8_malformed_markdown_exit_4_stripped(self, mock_run, mock_server,
                                                   tmp_path, capsys):
        from omniforge_mcp_server import main
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(
            0, json.dumps({"markdown": "garbage", "url": "/uploads/x"}))

        code = main(["--selftest", "--selftest-project", repo])

        assert code == 4
        assert capsys.readouterr().out.splitlines() == [
            "BOUNDARY VERDICT: STRIPPED"]

    @patch("omniforge_mcp_server.mcp_server")
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s8_glab_failure_exit_3_failed(self, mock_run, mock_server,
                                           tmp_path, capsys):
        from omniforge_mcp_server import main
        repo = _make_repo(tmp_path)
        mock_run.return_value = _make_result(1, stderr="500")

        code = main(["--selftest", "--selftest-project", repo])

        assert code == 3
        assert capsys.readouterr().out.splitlines() == [
            "BOUNDARY VERDICT: FAILED"]
        mock_server.run.assert_not_called()

    @patch("omniforge_mcp_server.mcp_server")
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s8_upload_raised_exit_3_failed(self, mock_run, mock_server,
                                             tmp_path, capsys):
        from omniforge_mcp_server import main
        repo = _make_repo(tmp_path)
        mock_run.side_effect = RuntimeError("boom")

        code = main(["--selftest", "--selftest-project", repo])

        assert code == 3
        assert capsys.readouterr().out.splitlines() == [
            "BOUNDARY VERDICT: FAILED"]

    @patch("omniforge_mcp_server.mcp_server")
    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s8_missing_project_exit_2_no_subprocess(self, mock_run,
                                                     mock_server, capsys):
        from omniforge_mcp_server import main

        with pytest.raises(SystemExit) as exc:
            main(["--selftest"])

        assert exc.value.code == 2
        assert mock_run.call_count == 0  # refused before any subprocess
        mock_server.run.assert_not_called()
        err = capsys.readouterr().err
        assert "usage:" in err
        assert "--selftest-project" in err

    @patch("omniforge_mcp_server.mcp_server")
    def test_s8_no_flags_runs_mcp_server_exactly_as_today(self, mock_server):
        from omniforge_mcp_server import main

        main([])

        mock_server.run.assert_called_once_with()  # byte-identical call

    def test_s8_selftest_png_is_a_valid_1x1_png(self):
        # decoder check, not a vibe: walk every chunk, verify lengths,
        # CRC32s, IHDR dims/depth/color, IEND, and that IDAT inflates to
        # the exact scanline
        import zlib
        from omniforge_mcp_server import build_selftest_png
        data = build_selftest_png()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        chunks = []
        pos = 8
        while pos < len(data):
            length = int.from_bytes(data[pos:pos + 4], "big")
            tag = data[pos + 4:pos + 8]
            payload = data[pos + 8:pos + 8 + length]
            crc = int.from_bytes(data[pos + 8 + length:pos + 12 + length],
                                 "big")
            assert crc == zlib.crc32(tag + payload)
            chunks.append((tag, payload))
            pos += 12 + length
        assert pos == len(data)  # no trailing garbage
        assert chunks[0][0] == b"IHDR"
        ihdr = chunks[0][1]
        assert int.from_bytes(ihdr[0:4], "big") == 1  # width
        assert int.from_bytes(ihdr[4:8], "big") == 1  # height
        assert ihdr[8] == 8                            # bit depth
        assert ihdr[9] in (2, 6)                       # color type RGB(A)
        assert chunks[-1] == (b"IEND", b"")
        idat = next(p for t, p in chunks if t == b"IDAT")
        assert zlib.decompress(idat) == b"\x00\xff\x00\x00"  # filter+RGB pixel

    @patch("omniforge_mcp_server.run_exec", new_callable=AsyncMock)
    def test_s8_upload_argv_pins_boundary_header_verbatim(self, mock_run,
                                                          tmp_path):
        # the glab argv must carry the boundary where glab can use it:
        # -H "Content-Type: <value>" (glab api rejects a bare value)
        from omniforge_mcp_server import _upload_project_file
        repo = _make_repo(tmp_path)
        img = tmp_path / "shot.png"
        img.write_bytes(b"png")
        captured = {}

        async def fake_run(args, cwd=None, timeout=60, env=None):
            captured["header"] = args[args.index("-H") + 1]
            return _make_result(0, UPLOAD_JSON)

        mock_run.side_effect = fake_run

        result = asyncio.run(_upload_project_file(str(img), repo))
        assert result["success"] is True
        assert captured["header"] == (
            "Content-Type: multipart/form-data; boundary=omniforge-upload-3f2a8c1d")

    def test_s8_real_main_subprocess_ok(self, tmp_path):
        # the REAL __main__ dispatch, hermetic: a stub glab is shadowed on
        # PATH (it records its argv + request body and answers like the
        # uploads endpoint; it never touches the network)
        import subprocess
        import textwrap
        from omniforge_mcp_server import build_selftest_png
        server_py = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "tools",
            "omniforge_mcp_server.py"))
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        record = tmp_path / "glab_calls.jsonl"
        stub = stub_dir / "glab"
        stub.write_text(textwrap.dedent('''\
            #!/usr/bin/env python3
            import json, os, sys
            entry = {"argv": sys.argv}
            if "--input" in sys.argv:
                with open(sys.argv[sys.argv.index("--input") + 1], "rb") as fh:
                    entry["body"] = fh.read().decode("latin1")
            with open(os.environ["GLAB_STUB_RECORD"], "a") as fh:
                fh.write(json.dumps(entry) + "\\n")
            if "uploads" in sys.argv:
                sys.stdout.write(json.dumps({
                    "markdown": "![x](/uploads/a.png)",
                    "url": "/uploads/a.png"}))
        '''))
        stub.chmod(0o755)
        repo = _make_repo(tmp_path)
        env = os.environ.copy()
        env["PATH"] = str(stub_dir) + os.pathsep + env.get("PATH", "")
        env["GLAB_STUB_RECORD"] = str(record)

        proc = subprocess.run(
            [sys.executable, server_py, "--selftest",
             "--selftest-project", repo],
            capture_output=True, text=True, env=env, timeout=60)

        assert proc.returncode == 0
        assert proc.stdout.splitlines() == ["BOUNDARY VERDICT: OK"]
        entries = [json.loads(line) for line in record.read_text().splitlines()]
        upload = next(e for e in entries if "uploads" in e["argv"])
        header = upload["argv"][upload["argv"].index("-H") + 1]
        assert header == (
            "Content-Type: multipart/form-data; boundary=omniforge-upload-3f2a8c1d")
        # the request body is the multipart framing around the selftest PNG
        body = upload["body"].encode("latin1")
        assert body.startswith(b"--omniforge-upload-3f2a8c1d\r\n")
        assert b"Content-Type: image/png\r\n" in body
        assert build_selftest_png() in body
        assert body.endswith(b"--omniforge-upload-3f2a8c1d--\r\n")
