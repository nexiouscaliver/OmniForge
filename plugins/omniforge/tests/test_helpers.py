"""Tests for OmniReview MCP server helper functions."""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from omnireview_mcp_server import (
    MAX_DIFF_LINES,
    extract_changed_files,
    parse_commits,
    run_exec,
    truncate_diff_if_needed,
)


# ── extract_changed_files ─────────────────────────────────


class TestExtractChangedFiles:
    def test_single_file(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new"
        assert extract_changed_files(diff) == ["foo.py"]

    def test_multiple_files(self):
        diff = (
            "+++ b/foo.py\n"
            "@@ -1 +1 @@\n"
            "+++ b/bar.py\n"
            "@@ -1 +1 @@\n"
        )
        assert extract_changed_files(diff) == ["foo.py", "bar.py"]

    def test_empty_string(self):
        assert extract_changed_files("") == []

    def test_paths_with_directories(self):
        diff = "+++ b/src/utils/helper.py\n@@ -1 +1 @@\n"
        assert extract_changed_files(diff) == ["src/utils/helper.py"]

    def test_no_duplicates(self):
        diff = "+++ b/foo.py\n+++ b/foo.py\n"
        assert extract_changed_files(diff) == ["foo.py"]


# ── parse_commits ─────────────────────────────────────────


class TestParseCommits:
    def test_single_commit(self):
        log = "abc1234 fix: repair the widget"
        result = parse_commits(log)
        assert result == [{"sha": "abc1234", "message": "fix: repair the widget"}]

    def test_multiple_commits(self):
        log = "abc1234 first commit\ndef5678 second commit"
        result = parse_commits(log)
        assert len(result) == 2
        assert result[0]["sha"] == "abc1234"
        assert result[1]["message"] == "second commit"

    def test_empty_string(self):
        assert parse_commits("") == []

    def test_sha_only_no_message(self):
        log = "abc1234"
        result = parse_commits(log)
        assert result == [{"sha": "abc1234", "message": ""}]

    def test_trailing_newlines_ignored(self):
        log = "abc1234 feat: first\ndef5678 fix: second\n\n"
        result = parse_commits(log)
        assert len(result) == 2
        assert result[0]["sha"] == "abc1234"
        assert result[1]["sha"] == "def5678"


# ── truncate_diff_if_needed ───────────────────────────────


class TestTruncateDiffIfNeeded:
    def test_short_diff_unchanged(self):
        diff = "line1\nline2\nline3"
        result, truncated = truncate_diff_if_needed(diff, 3)
        assert result == diff
        assert truncated is False

    def test_exact_limit_unchanged(self):
        lines = [f"line{i}" for i in range(MAX_DIFF_LINES)]
        diff = "\n".join(lines)
        result, truncated = truncate_diff_if_needed(diff, MAX_DIFF_LINES)
        assert result == diff
        assert truncated is False

    def test_long_diff_truncated(self):
        total = MAX_DIFF_LINES + 500
        lines = [f"line{i}" for i in range(total)]
        diff = "\n".join(lines)
        result, truncated = truncate_diff_if_needed(diff, total)
        assert truncated is True
        assert "TRUNCATED" in result
        assert str(total) in result
        assert str(MAX_DIFF_LINES) in result
        # Should only have MAX_DIFF_LINES worth of content lines
        result_lines = result.split("\n")
        # First MAX_DIFF_LINES lines + blank + truncation message
        assert result_lines[0] == "line0"
        assert result_lines[MAX_DIFF_LINES - 1] == f"line{MAX_DIFF_LINES - 1}"


# ── run_exec ──────────────────────────────────────────────


class TestRunExec:
    def test_successful_command(self):
        result = asyncio.run(
            run_exec(["echo", "hello"], cwd="/tmp")
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "hello"

    def test_failed_command(self):
        result = asyncio.run(
            run_exec(["false"], cwd="/tmp")
        )
        assert result.returncode != 0

    def test_stderr_capture(self):
        result = asyncio.run(
            run_exec(["ls", "/nonexistent_path_xyz"], cwd="/tmp")
        )
        assert result.returncode != 0
        assert result.stderr != ""

    def test_timeout_kills_process(self):
        result = asyncio.run(
            run_exec(["sleep", "30"], cwd="/tmp", timeout=1)
        )
        assert result.returncode == -1
        assert "timed out" in result.stderr.lower()
