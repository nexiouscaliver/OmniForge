"""Transport tests for omni_glab_api.py (W4 T1 + T2).

Host/token/redaction resolution plus the T2 additions: form encoding of the
literal position[...] bracket keys, retry classification, method pass-through
to urllib, get_all page validation, and the redaction wiring inside
request(). Stdlib only — no pytest import — so the module runs identically
under bare unittest and pytest.
"""

import os
import subprocess
import sys
import unittest
import urllib.parse
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


class TransportRequestTests(unittest.TestCase):
    """T2 additions: the poster's payload/retry contract on the transport."""

    def test_form_encoding_position_keys(self):
        # Literal bracket keys must survive urlencode/parse_qs round-trip —
        # this is the property glab's --raw-field flag layer loses.
        form = [("body", "**Important** — x"),
                ("position[position_type]", "text"),
                ("position[base_sha]", "aaa111"),
                ("position[start_sha]", "ccc333"),
                ("position[head_sha]", "bbb222"),
                ("position[new_path]", "src/app.py"),
                ("position[old_path]", "src/app.py"),
                ("position[new_line]", "42")]
        data = urllib.parse.urlencode(form)
        parsed = urllib.parse.parse_qs(data)
        for key, value in form:
            self.assertEqual(parsed[key], [value], key)

    def test_retry_classification(self):
        # 5xx and 429 retry with base*2^(attempt-1); 400/404 fail fast with
        # no sleep; exhaustion raises retryable=True.
        cases = [
            ([500, 200], "ok", [2.0]),
            ([429, 200], "ok", [2.0]),
            ([503, 503, 200], "ok", [2.0, 4.0]),
            ([400], "err", []),
            ([404], "err", []),
            ([500, 500, 500], "err", [2.0, 4.0]),
        ]
        for scripted, expected, sleeps in cases:
            with self.subTest(scripted=scripted):
                state = {"n": 0}

                def fake_http(url, headers, data=None, method=None):
                    status = scripted[state["n"]]
                    state["n"] += 1
                    return status, "HTTP %d: server error body" % status

                recorded = []
                with mock.patch.object(omni_glab_api, "_http", fake_http), \
                        mock.patch.object(omni_glab_api, "sleep_fn",
                                          recorded.append):
                    if expected == "ok":
                        resp = omni_glab_api.request(
                            "POST", "/projects/1/merge_requests/2/discussions",
                            "tok", form=[("body", "b")])
                        self.assertEqual(resp["status"], 200)
                    else:
                        with self.assertRaises(omni_glab_api.GlabApiError) \
                                as cm:
                            omni_glab_api.request(
                                "POST",
                                "/projects/1/merge_requests/2/discussions",
                                "tok", form=[("body", "b")])
                        if scripted[-1] >= 500 or scripted[-1] == 429:
                            self.assertTrue(cm.exception.retryable)
                        else:
                            self.assertFalse(cm.exception.retryable)
                self.assertEqual(recorded, sleeps)

    def test_method_param_reaches_http_request(self):
        # request(method=...) must drive the urllib verb — NOT be derived
        # from data presence (a DELETE or empty-body POST must not become a
        # GET). End-to-end through the real _http with urlopen mocked.
        seen = []

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def getcode(self):
                return 200

            def read(self):
                return b"{}"

        def fake_urlopen(req, timeout=None):
            seen.append(req.get_method())
            return FakeResp()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            omni_glab_api.request("DELETE", "/projects/1", "tok")
            omni_glab_api.request("POST", "/projects/1/notes", "tok")  # no form
            omni_glab_api.request("PUT", "/projects/1", "tok",
                                  form=[("resolved", "true")])
        self.assertEqual(seen, ["DELETE", "POST", "PUT"])

    def test_user_agent_header_sent(self):
        # gitlab.com's Cloudflare edge challenges the default Python-urllib
        # UA; every request must carry the explicit User-Agent.
        seen = []

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def getcode(self):
                return 200

            def read(self):
                return b"{}"

        def fake_urlopen(req, timeout=None):
            seen.append(req.headers.get("User-agent"))
            return FakeResp()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            omni_glab_api.request("GET", "/projects/1", "tok")
        self.assertEqual(seen, [omni_glab_api.USER_AGENT])

    def test_get_all_raises_on_unparseable_page(self):
        # A 200 whose body is not a JSON array must raise, never silently
        # terminate pagination as an empty page.
        with mock.patch.object(omni_glab_api, "_http",
                               lambda url, headers, data=None, method=None:
                               (200, "<html>gateway oops</html>")):
            with self.assertRaises(omni_glab_api.GlabApiError) as cm:
                omni_glab_api.get_all("/projects/1/merge_requests/2/notes",
                                      "tok")
        self.assertIn("not a JSON array", str(cm.exception))

    def test_request_error_detail_redacted(self):
        # The redaction wiring inside request(): a GlabApiError carries a
        # redacted detail body (the token never reaches the message).
        secret = "tok-super-secret"
        with mock.patch.object(
                omni_glab_api, "_http",
                lambda url, headers, data=None, method=None:
                (400, "bad request for " + secret)):
            with self.assertRaises(omni_glab_api.GlabApiError) as cm:
                omni_glab_api.request("POST", "/projects/1/notes", secret,
                                      form=[("body", "b")])
        self.assertNotIn(secret, str(cm.exception))
        self.assertNotIn(secret, cm.exception.detail)
        self.assertIn("***", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
