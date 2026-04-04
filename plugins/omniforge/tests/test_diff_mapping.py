"""Tests for map_diff_lines: parse diff to extract exact changed line numbers per file."""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))


# ── parse_diff_line_map Tests ─────────────────────────────

SINGLE_FILE_DIFF = """\
diff --git a/.gitlab-ci.yml b/.gitlab-ci.yml
index cd48b40..a1c70fc 100644
--- a/.gitlab-ci.yml
+++ b/.gitlab-ci.yml
@@ -1069,13 +1069,34 @@ deploy_to_ecs_staging:
       check_not_empty STAGING_DB_PASSWORD
       check_not_empty STAGING_DB_NAME
       check_not_empty STAGING_DB_ENABLED
+      # Stripe Configuration (staging)
+      check_not_empty STRIPE_SECRET_KEY_STAGING
+      check_not_empty STRIPE_PUBLISHABLE_KEY_STAGING
       if [ "$validation_failed" = true ]; then
-        printf "OLD ERROR MESSAGE"
+        printf "NEW ERROR MESSAGE"
       fi
"""

MULTI_FILE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index aaa..bbb 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,6 +10,8 @@ def main():
     print("hello")
+    validate_input()
+    check_auth()
     return True
diff --git a/src/utils.py b/src/utils.py
index ccc..ddd 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -1,3 +1,5 @@
+import os
+import sys
 def helper():
     pass
@@ -20,4 +22,5 @@ def other():
     x = 1
+    y = 2
     return x
"""

EMPTY_DIFF = ""

DELETE_ONLY_DIFF = """\
diff --git a/old.py b/old.py
index aaa..bbb 100644
--- a/old.py
+++ b/old.py
@@ -5,7 +5,5 @@ def func():
     a = 1
-    b = 2
-    c = 3
     return a
"""


class TestParseDiffLineMap:
    def test_single_file_added_lines(self):
        from omnireview_mcp_server import parse_diff_line_map
        result = parse_diff_line_map(SINGLE_FILE_DIFF)
        assert ".gitlab-ci.yml" in result
        ci = result[".gitlab-ci.yml"]
        # Lines with + prefix are added lines
        assert "added_lines" in ci
        # Line 1072 = "# Stripe Configuration (staging)" (1069 + 3 context lines)
        assert 1072 in ci["added_lines"]
        assert 1073 in ci["added_lines"]  # STRIPE_SECRET_KEY_STAGING
        assert 1074 in ci["added_lines"]  # STRIPE_PUBLISHABLE_KEY_STAGING

    def test_single_file_modified_lines(self):
        from omnireview_mcp_server import parse_diff_line_map
        result = parse_diff_line_map(SINGLE_FILE_DIFF)
        ci = result[".gitlab-ci.yml"]
        # The replaced printf line should also be in added_lines
        # (it's a new line replacing the old one)
        added = ci["added_lines"]
        assert any(line >= 1075 for line in added)  # NEW ERROR MESSAGE line

    def test_single_file_hunks(self):
        from omnireview_mcp_server import parse_diff_line_map
        result = parse_diff_line_map(SINGLE_FILE_DIFF)
        ci = result[".gitlab-ci.yml"]
        assert "hunks" in ci
        assert len(ci["hunks"]) >= 1
        # Each hunk has new_start and new_count
        hunk = ci["hunks"][0]
        assert hunk["new_start"] == 1069
        assert hunk["new_count"] == 34

    def test_multi_file(self):
        from omnireview_mcp_server import parse_diff_line_map
        result = parse_diff_line_map(MULTI_FILE_DIFF)
        assert "src/app.py" in result
        assert "src/utils.py" in result

    def test_multi_file_lines(self):
        from omnireview_mcp_server import parse_diff_line_map
        result = parse_diff_line_map(MULTI_FILE_DIFF)
        app = result["src/app.py"]
        # validate_input() at line 11, check_auth() at line 12
        assert 11 in app["added_lines"]
        assert 12 in app["added_lines"]

    def test_multi_file_multiple_hunks(self):
        from omnireview_mcp_server import parse_diff_line_map
        result = parse_diff_line_map(MULTI_FILE_DIFF)
        utils = result["src/utils.py"]
        # Two hunks in utils.py
        assert len(utils["hunks"]) == 2
        assert utils["hunks"][0]["new_start"] == 1
        assert utils["hunks"][1]["new_start"] == 22

    def test_multi_file_second_hunk_lines(self):
        from omnireview_mcp_server import parse_diff_line_map
        result = parse_diff_line_map(MULTI_FILE_DIFF)
        utils = result["src/utils.py"]
        # "import os" at line 1, "import sys" at line 2
        assert 1 in utils["added_lines"]
        assert 2 in utils["added_lines"]
        # "y = 2" in second hunk at line 23 (22=context "x=1", 23=added "y=2")
        assert 23 in utils["added_lines"]

    def test_empty_diff(self):
        from omnireview_mcp_server import parse_diff_line_map
        result = parse_diff_line_map(EMPTY_DIFF)
        assert result == {}

    def test_delete_only_no_added_lines(self):
        from omnireview_mcp_server import parse_diff_line_map
        result = parse_diff_line_map(DELETE_ONLY_DIFF)
        old = result.get("old.py", {})
        # Only deletions, no added lines
        assert len(old.get("added_lines", [])) == 0

    def test_context_lines_not_in_added(self):
        from omnireview_mcp_server import parse_diff_line_map
        result = parse_diff_line_map(SINGLE_FILE_DIFF)
        ci = result[".gitlab-ci.yml"]
        # Context lines (no + prefix) should NOT be in added_lines
        # Line 1069 = "check_not_empty STAGING_DB_PASSWORD" (context)
        assert 1069 not in ci["added_lines"]

    def test_returns_all_lines_in_diff(self):
        from omnireview_mcp_server import parse_diff_line_map
        result = parse_diff_line_map(SINGLE_FILE_DIFF)
        ci = result[".gitlab-ci.yml"]
        # all_new_lines includes context + added (everything visible in the new file)
        assert "all_new_lines" in ci
        assert 1069 in ci["all_new_lines"]  # context line
        assert 1072 in ci["all_new_lines"]  # added line
        assert len(ci["all_new_lines"]) > len(ci["added_lines"])
