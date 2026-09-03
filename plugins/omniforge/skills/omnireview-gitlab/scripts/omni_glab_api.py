#!/usr/bin/env python3
"""omni_glab_api.py — shared GitLab REST transport (W4 T1/T2).

Direct stdlib-urllib REST for the omnireview-gitlab scripts: one
transport/auth/retry/host contract, tested once. Eliminates per-call glab
process spawns AND the glab --raw-field nested-key loss class (the root
cause of unanchored inline threads).

Contract:
- resolve_host: --host flag > GITLAB_HOST env > CI_API_V4_URL env (verbatim
  when it starts with http) > https://gitlab.com.
- api_base: host itself when it already ends /api/v4 (the CI_API_V4_URL
  case), else <host>/api/v4.
- resolve_token: GITLAB_TOKEN env > OMNIFORGE_GITLAB_TOKEN env > None.
- auth_header: Authorization: Bearer <token> (AUTH_HEADER_KIND constant;
  deterministic flip to PRIVATE-TOKEN per the W4 spec cross-cutting 2 —
  dev-exercised only, never first-fired inside A/B). Bearer dev-verified
  HTTP 200 on project 73279395 on 2026-09-03.
- request: ONE call with bounded retry on 5xx/429/URLError/timeout,
  backoff_base * 2^(attempt-1) (2 s then 4 s at defaults); fail-fast
  GlabApiError on other 4xx naming method+path+status (never the token —
  detail bodies are redacted). Returns {"status", "body", "json"}.
- get_all: paginated per_page=100&page=N concatenation until a page
  returns fewer than 100 items.
- redact: replaces any token occurrence with ***.

Token values are never logged. Stdlib only.
"""

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_HEADER_KIND = "Bearer"
DEFAULT_HOST = "https://gitlab.com"
HTTP_TIMEOUT_SECS = 60
# gitlab.com's Cloudflare edge serves a managed challenge (HTTP 403 HTML)
# to the default "Python-urllib/x" User-Agent; an explicit UA passes.
USER_AGENT = "omniforge-glab-api/3.3.1"

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
    """--host flag > GITLAB_HOST env > CI_API_V4_URL env (verbatim) >
    https://gitlab.com."""
    host = host_flag or os.environ.get("GITLAB_HOST")
    if host:
        return host if host.startswith("http") else "https://" + host
    ci = os.environ.get("CI_API_V4_URL")
    if ci and ci.startswith("http"):
        return ci
    return DEFAULT_HOST


def api_base(host):
    """API base URL: the host itself when it already ends /api/v4 (the
    CI_API_V4_URL case), else <host>/api/v4."""
    h = host if host.startswith("http") else "https://" + host
    h = h.rstrip("/")
    return h if h.endswith("/api/v4") else h + "/api/v4"


def resolve_token():
    """GITLAB_TOKEN env > OMNIFORGE_GITLAB_TOKEN env > None."""
    return os.environ.get("GITLAB_TOKEN") or \
        os.environ.get("OMNIFORGE_GITLAB_TOKEN") or None


def auth_header(token):
    """(name, value) header pair for the shipped auth kind."""
    if AUTH_HEADER_KIND == "Bearer":
        return ("Authorization", "Bearer " + token)
    return ("PRIVATE-TOKEN", token)


def redact(text, token):
    """Replace any token occurrence with *** (never log token values)."""
    if token and text:
        return text.replace(token, "***")
    return text


def _http(url, headers, data=None, method=None):
    """ONE urllib round trip -> (status, body). urllib.error.URLError /
    socket.timeout propagate; urllib.error.HTTPError is unwound into its
    (code, body). Module-level seam so tests can script responses. The
    verb comes from the caller's `method` (a bodyless DELETE/POST must not
    silently become a GET), falling back to data presence."""
    req = urllib.request.Request(
        url, data=data, headers=headers,
        method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECS) as r:
            return r.getcode(), r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def _parse_json(body):
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return None


def request(method, path, token, host=None, form=None, attempts=3,
            backoff_base=2.0, sleep_fn=None):
    """ONE API call with bounded retry.

    Returns {"status": int, "body": str, "json": parsed-or-None}. Retries
    5xx/429/URLError/timeout with backoff_base * 2**(attempt-1); raises
    GlabApiError(method, path, status, redacted detail) on 4xx fail-fast
    and on retry exhaustion (retryable=True so callers may page-retry).
    """
    _sleep = sleep_fn if sleep_fn is not None else globals()["sleep_fn"]
    url = api_base(resolve_host(host)) + path
    data = urllib.parse.urlencode(form).encode("utf-8") if form else None
    name, value = auth_header(token)
    headers = {name: value, "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    last_status, last_body = 0, ""
    for attempt in range(1, attempts + 1):
        try:
            status, body = _http(url, headers, data, method=method)
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            status, body = 0, str(e)
        if 200 <= status < 300:
            return {"status": status, "body": body, "json": _parse_json(body)}
        last_status, last_body = status, body
        if status == 0 or status == 429 or status >= 500:
            if attempt < attempts:
                _sleep(backoff_base * (2 ** (attempt - 1)))
                continue
            raise GlabApiError(method, path, status, redact(last_body, token),
                               retryable=True)
        raise GlabApiError(method, path, status, redact(last_body, token),
                           retryable=False)
    raise GlabApiError(method, path, last_status, redact(last_body, token),
                       retryable=True)


def get_all(path, token, host=None, attempts=3, backoff_base=2.0):
    """Paginate per_page=100&page=N until a page returns fewer than 100
    items; returns the concatenated array."""
    items, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        resp = request("GET", "%s%sper_page=100&page=%d" % (path, sep, page),
                       token, host=host, attempts=attempts,
                       backoff_base=backoff_base)
        batch = resp.get("json")
        if not isinstance(batch, list):
            # A 200 whose body is not a JSON array must never silently
            # terminate pagination as an empty page.
            raise GlabApiError("GET", path, resp.get("status", 0),
                               "paginated response body is not a JSON array",
                               retryable=False)
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1
