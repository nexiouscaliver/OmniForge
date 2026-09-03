"""Transport resolution tests for omni_glab_api.py (W4 T1).

Host/token/redaction only — the retry/form-encoding tests land with T2's
full poster rewrite. Stdlib only — no pytest import — so the module runs
identically under bare unittest and pytest.
"""

import os
import subprocess
import sys
import unittest
from unittest import mock

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
    "skills", "omnireview-gitlab", "scripts"))
FETCH = os.path.join(SCRIPTS, "omni_fetch_mr.py")

sys.path.insert(0, SCRIPTS)
import omni_glab_api  # noqa: E402


def clean_env():
    return {k: v for k, v in os.environ.items()
            if k not in ("GITLAB_TOKEN", "OMNIFORGE_GITLAB_TOKEN",
                         "GITLAB_HOST", "CI_API_V4_URL")}


class TransportResolutionTests(unittest.TestCase):
    def test_host_resolution_order(self):
        # --host flag wins over every env var
        with mock.patch.dict(os.environ, {"GITLAB_HOST": "https://env.example.com"},
                             clear=False):
            os.environ.pop("CI_API_V4_URL", None)
            self.assertEqual(omni_glab_api.resolve_host("https://flag.example.com"),
                             "https://flag.example.com")
            self.assertEqual(omni_glab_api.resolve_host(None),
                             "https://env.example.com")
        # GITLAB_HOST env > CI_API_V4_URL env
        with mock.patch.dict(os.environ, {
                "GITLAB_HOST": "https://env.example.com",
                "CI_API_V4_URL": "https://ci.example.com/api/v4"},
                             clear=False):
            self.assertEqual(omni_glab_api.resolve_host(None),
                             "https://env.example.com")
        # CI_API_V4_URL used VERBATIM (already carries /api/v4) — api_base
        # must not append a second /api/v4
        env = clean_env()
        env["CI_API_V4_URL"] = "https://ci.example.com/api/v4"
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(omni_glab_api.resolve_host(None),
                             "https://ci.example.com/api/v4")
            self.assertEqual(omni_glab_api.api_base(
                omni_glab_api.resolve_host(None)),
                "https://ci.example.com/api/v4")
        # default host + api_base join
        env = clean_env()
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(omni_glab_api.resolve_host(None),
                             "https://gitlab.com")
            self.assertEqual(omni_glab_api.api_base("https://gitlab.com"),
                             "https://gitlab.com/api/v4")
            self.assertEqual(omni_glab_api.api_base("https://gitlab.com/"),
                             "https://gitlab.com/api/v4")
        # Bearer is the shipped header kind (dev-verified 200 on the scratch
        # project); PRIVATE-TOKEN is the deterministic fallback flip
        self.assertEqual(omni_glab_api.AUTH_HEADER_KIND, "Bearer")

    def test_token_resolution_and_missing_error(self):
        env = clean_env()
        env["OMNIFORGE_GITLAB_TOKEN"] = "tok-fallback"
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(omni_glab_api.resolve_token(), "tok-fallback")
        env["GITLAB_TOKEN"] = "tok-primary"
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(omni_glab_api.resolve_token(), "tok-primary")
        env = clean_env()
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertIsNone(omni_glab_api.resolve_token())
            # missing token drives the CALLER's exit-2 contract with the
            # glab extraction fix on stderr and no stdout JSON
            proc = subprocess.run(
                [sys.executable, FETCH, "--project", "1", "--mr", "2",
                 "--out", "/tmp/never_written_w4_t1.json"],
                env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("glab auth status", proc.stderr)
            self.assertEqual(proc.stdout.strip(), "")

    def test_error_redacts_token(self):
        self.assertEqual(
            omni_glab_api.redact(
                "Authorization: Bearer sekrit-value path /x", "sekrit-value"),
            "Authorization: Bearer *** path /x")
        self.assertEqual(omni_glab_api.redact("plain text", None), "plain text")
        self.assertEqual(omni_glab_api.redact("no match here", "zzz"),
                         "no match here")


if __name__ == "__main__":
    unittest.main()
