#!/usr/bin/env python3
"""omni_glab_api.py — shared GitLab REST transport (W4 T1/T2).

RED-commit skeleton stub: module constants, the GlabApiError shape, and the
Produces-block signatures. Every function body raises NotImplementedError;
the implementation lands in the GREEN commit.
"""

import time

AUTH_HEADER_KIND = "Bearer"

sleep_fn = time.sleep          # module-level so tests can inject a recorder


class GlabApiError(Exception):
    """API call failure: message names method+path+status, never the token.

    retryable=True marks retry exhaustion (5xx/429/network) as opposed to a
    fail-fast 4xx, so callers can apply their own page-level retry policy.
    """

    def __init__(self, method, path, status, detail="", retryable=False):
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail
        self.retryable = retryable
        super().__init__("%s %s failed with HTTP %s: %s" % (
            method, path, status, detail))


def resolve_host(host_flag=None):
    raise NotImplementedError("resolve_host lands in the GREEN commit")


def api_base(host):
    raise NotImplementedError("api_base lands in the GREEN commit")


def resolve_token():
    raise NotImplementedError("resolve_token lands in the GREEN commit")


def auth_header(token):
    raise NotImplementedError("auth_header lands in the GREEN commit")


def _http(url, headers, data=None):
    raise NotImplementedError("_http lands in the GREEN commit")


def request(method, path, token, host=None, form=None, attempts=3,
            backoff_base=2.0, sleep_fn=None):
    raise NotImplementedError("request lands in the GREEN commit")


def get_all(path, token, host=None, attempts=3, backoff_base=2.0):
    raise NotImplementedError("get_all lands in the GREEN commit")


def redact(text, token):
    raise NotImplementedError("redact lands in the GREEN commit")
