"""Contract tests for the det-scan v1 evidence consumer
(omni_det_scan.py): the T1 PURE packet layer plus the T2 network engine.

The module is loaded IN-PROCESS via importlib (the load_poster_module
convention). T1 pins the pure half — no transport, no CLI:

- load_packet — OSError -> UsageError "packet-unreadable"; ValueError (JSON
  decode AND UnicodeDecodeError) -> UsageError "packet-not-json".
- validate_packet — first failure wins: not-a-dict root, then unknown
  TOP-LEVEL keys (named in the reason, fail-closed on schema evolution),
  then TYPE-STRICT schema_version int 1 (int 2, string "1", float 1.0 all
  reject), then mr/scan/findings/meta nested presence+type+enum checks
  returning "bad-field:<locus>". Unknown NESTED keys are TOLERATED
  everywhere (v1 additive tolerance); empty findings is a valid clean scan.
- packet_mr_matches — iid (and, for a numeric --project, project_id)
  compared as INTEGERS after coercion; ANY coercion failure is a clean
  False, never a traceback (A5c).
- map_severity — critical->critical, high->important, medium/low->minor.
- thread_body — EXACT FR-7 template: mapped **Critical|Important|Minor**
  marker + RAW packet severity + verbatim preview + the needs_judgment
  disposition, and NEVER "Confidence: "/"Found by: " (A4: those strings
  would flip is_omniforge_note's conjunction and route det-scan threads
  into the never-re-adjudicate priors channel).
- summary_body — EXACT FR-11 template: status+reason, one line per tool,
  findings counts, capped/overflow, redaction, the needs_judgment
  explainer; the Unanchored section renders ONLY when U > 0; NO severity
  marker tokens anywhere (the summary must not pollute extract_severity).

T2 pins the network engine (hermetic — omni_glab_api.request monkeypatched
with the FakeTransport from test_omni_post_review.py, same patcher pattern):

- head_sha_matches — CASE-INSENSITIVE packet-vs-MR head compare; empty on
  either side never matches (FR-4).
- fetch_anchor_map — the FR-8 three-step reuse chain over PAGINATED item
  arrays: omni_glab_api.get_all(<mr_path>/diffs) ->
  omni_fetch_mr.assemble_diff(items) -> omni_fetch_mr.parse_diff_line_map
  (parsing/assembly NEVER re-implemented; NEVER a since-sha diff); page 1
  carries EXACTLY 100 items (get_all's page-turn condition).
- anchorable / split_findings — file in map AND line in added_lines;
  (anchored, unanchored) preserves packet order and never silently drops
  the unanchorable (returned for disclosure, FR-8).
- newer_det_scan_note — mirrors omni_post_review.newer_summary_note
  EXACTLY: paginated notes listing, lstrip().startswith(DET_SCAN_PREFIX)
  (a mid-body occurrence never trips the guard), iso_to_epoch None
  semantics, note returned iff created is not None and created > since
  (FR-3, SC-2b).

T3 pins the CLI (main() under the raw_poster convention — controlled env,
redirected stdout/stderr, FakeTransport). Guard order is schema (exit 2, no
stdout) -> staleness (3) -> scan-skipped (exit-0 no-op, ZERO network) ->
token (2, AFTER all local guards — A5d) -> head-sha (3) -> dedup (3) ->
post; posting is summary-first then threads in packet order; dry-run emits
N+1 plan lines with the sibling PLACEHOLDER_REFS and ZERO transport calls;
every 0/1/3 exit prints exactly ONE 13-key JSON receipt line (exit 2 never
does). Packet fixtures are COPIED into a per-test tmp dir so os.utime mtime
control and head-sha variants never touch the repo.
"""

import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "skills",
    "omnireview-gitlab", "scripts"))
FIXTURES = os.path.join(HERE, "fixtures")
DETFILTER = os.path.join(FIXTURES, "detfilter")
DET_SCAN = os.path.join(SCRIPTS, "omni_det_scan.py")


def load_det_scan_module():
    """In-process loader (pure-function checks; proves the sibling imports
    omni_glab_api/omni_post_review/omni_fetch_mr load side-effect-free)."""
    spec = importlib.util.spec_from_file_location("omni_det_scan_under_test",
                                                  DET_SCAN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    omni_det_scan = load_det_scan_module()
except FileNotFoundError:
    omni_det_scan = None  # RED state: omni_det_scan.py does not exist yet


def _require_det_scan():
    if omni_det_scan is None:
        raise AssertionError(
            "omni_det_scan.py does not exist yet (T1 GREEN deliverable)")


def packet(name):
    """Load a detfilter packet fixture as a dict."""
    with open(os.path.join(DETFILTER, name), encoding="utf-8") as fh:
        return json.load(fh)


# The canonical finding (detfilter_packet_ok.json findings[0], inline so
# render tests cannot drift with the fixture).
FINDING = {"tool": "gitleaks", "rule_id": "aws-access-key",
           "file": "src/app.py", "line": 42, "severity": "critical",
           "class": "secret",
           "preview_redacted": "AWS Access Key ID=REDACTED:secret:ab12cd34"}

# ── T2 engine test-side machinery (FakeTransport + constants, copied from
# tests/test_omni_post_review.py — same contract, same patcher pattern) ─────

sys.path.insert(0, SCRIPTS)
import omni_glab_api  # noqa: E402  (the SAME in-process instance the module
                      # under test resolves omni_glab_api.get_all through —
                      # patching this object's request covers both)

PROJECT = "73279395"
MR = "136"
MR_PATH = "projects/%s/merge_requests/%s" % (PROJECT, MR)

NOTES_PAGE1 = MR_PATH + "/notes?per_page=100&page=1"
NOTES_PAGE2 = MR_PATH + "/notes?per_page=100&page=2"
DIFFS_PAGE1 = MR_PATH + "/diffs?per_page=100&page=1"
DIFFS_PAGE2 = MR_PATH + "/diffs?per_page=100&page=2"

DIFF_REFS = {"diff_refs": {"base_sha": "aaa111", "head_sha": "bbb222",
                           "start_sha": "ccc333"}}

# created_at of the seeded det-scan note in DET_SCAN_NOTE.
DET_SCAN_EPOCH = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc).timestamp()

DET_SCAN_NOTE = {"body": "det-scan: scanner evidence — MR !136 @ bbb222",
                 "created_at": "2026-09-02T10:00:00Z"}


def run_env(token="tok-test"):
    """A clean env for main() runs: token vars replaced by `token` (None
    removes both), host envs stripped — the raw_poster convention."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("GITLAB_TOKEN", "OMNIFORGE_GITLAB_TOKEN",
                        "GITLAB_HOST", "CI_API_V4_URL")}
    if token is not None:
        env["GITLAB_TOKEN"] = token
    return env


class FakeTransport:
    """Stand-in for omni_glab_api.request honoring the same contract:
    bounded retry (attempts) on 429/5xx with backoff_base * 2^(attempt-1)
    recorded into self.sleeps, fail-fast GlabApiError on other 4xx, and
    {"status","body","json"} on success. Every attempt is recorded in
    self.calls as (method, path, form). GET fixtures are served by exact
    path from self.gets; POST failure sequences by exact path from
    self.scripts (one item consumed per attempt — an int status, or a
    (status, payload) tuple scripting a 2xx WITH a JSON body; exhausted or
    absent => success); static 2xx POST bodies by exact path from
    self.posts (checked AFTER scripts so failure scripts still win).
    """

    def __init__(self):
        self.calls = []
        self.hosts = []          # host arg per request (parallel to calls)
        self.gets = {}
        self.scripts = {}
        self.posts = {}
        self.sleeps = []

    def script(self, path, codes):
        self.scripts[path] = list(codes)

    def _attempt_status(self, method, path):
        if method == "GET" and path in self.gets:
            return 200, self.gets[path]
        codes = self.scripts.get(path)
        if codes:
            item = codes.pop(0)
            if isinstance(item, tuple):   # (status, payload) — seeded JSON
                return item
            return item, None
        if method == "POST" and path in self.posts:
            return 200, self.posts[path]
        return 200, {}

    def request(self, method, path, token, host=None, form=None, attempts=3,
                backoff_base=2.0, sleep_fn=None):
        for attempt in range(1, attempts + 1):
            self.calls.append((method, path, list(form or [])))
            self.hosts.append(host)
            status, payload = self._attempt_status(method, path)
            if 200 <= status < 300:
                return {"status": status, "body": json.dumps(payload),
                        "json": payload}
            err = omni_glab_api.GlabApiError(
                method, path, status, "HTTP %d: server error body" % status,
                retryable=(status == 429 or status >= 500))
            if err.retryable and attempt < attempts:
                self.sleeps.append(backoff_base * (2 ** (attempt - 1)))
                continue
            raise err
        raise AssertionError("unreachable")


def diffs_page(name):
    """Load a detfilter diffs-page fixture (the REAL array-of-items /diffs
    shape — never diff text; assemble_diff synthesizes the headers)."""
    with open(os.path.join(DETFILTER, name), encoding="utf-8") as fh:
        return json.load(fh)


class DetScanPacketTests(unittest.TestCase):

    def setUp(self):
        _require_det_scan()

    def test_valid_packet_passes_validation(self):
        self.assertIsNone(omni_det_scan.validate_packet(
            packet("detfilter_packet_ok.json")))
        # empty findings is a VALID clean scan (inline minimal packet)
        clean = {"schema_version": 1,
                 "mr": {"project_id": 73279395, "iid": 136,
                        "head_sha": "bbb222", "base_sha": "aaa111"},
                 "scan": {"status": "ok", "reason": "", "tools": []},
                 "findings": [],
                 "meta": {"capped": False,
                          "overflow_not_adjudicated": 0,
                          "redaction": "REDACTED:<type>:<hash8>"}}
        self.assertIsNone(omni_det_scan.validate_packet(clean))

    def test_unknown_top_level_field_rejected_names_field(self):
        # fail-closed on schema evolution: one extra top-level key rejects
        # naming the key in iteration order
        self.assertEqual(
            omni_det_scan.validate_packet(
                packet("detfilter_packet_unknown_top.json")),
            "unknown-top-level-field:extra")

    def test_bad_nested_enum_and_missing_field_name_locus(self):
        # a bad nested enum names the exact locus
        self.assertEqual(
            omni_det_scan.validate_packet(
                packet("detfilter_packet_bad_nested.json")),
            "bad-field:findings[1].severity")
        # a missing nested field names its locus too
        obj = packet("detfilter_packet_ok.json")
        del obj["findings"][0]["rule_id"]
        self.assertEqual(omni_det_scan.validate_packet(obj),
                         "bad-field:findings[0].rule_id")

    def test_unknown_nested_keys_tolerated(self):
        # v1 additive tolerance: unknown NESTED keys never reject
        self.assertIsNone(omni_det_scan.validate_packet(
            packet("detfilter_packet_additive.json")))

    def test_schema_version_must_be_1(self):
        # TYPE-STRICT: 1.0 == 1 in Python, but float and string reject
        base = packet("detfilter_packet_ok.json")
        for bad in (2, "1", 1.0):
            obj = dict(base, schema_version=bad)
            self.assertEqual(omni_det_scan.validate_packet(obj),
                             "unsupported-schema-version", repr(bad))

    def test_load_packet_failure_reasons(self):
        # non-JSON text and invalid UTF-8 are both packet-not-json
        # (UnicodeDecodeError is a ValueError); a directory path is
        # packet-unreadable (IsADirectoryError ⊂ OSError, root-safe)
        for name in ("detfilter_packet_not_json.txt",
                     "detfilter_packet_unreadable.bin"):
            with self.assertRaises(omni_det_scan.UsageError) as cm:
                omni_det_scan.load_packet(os.path.join(DETFILTER, name))
            self.assertIn("packet-not-json", str(cm.exception), name)
        with self.assertRaises(omni_det_scan.UsageError) as cm:
            omni_det_scan.load_packet(DETFILTER)
        self.assertIn("packet-unreadable", str(cm.exception))

    def test_packet_mr_mismatch_and_coercion(self):
        ok = packet("detfilter_packet_ok.json")
        # integer coercion: packet iid 136 vs string --mr "136"
        self.assertTrue(omni_det_scan.packet_mr_matches(ok, "136",
                                                        "73279395"))
        # a different MR mismatches
        other = packet("detfilter_packet_other_mr.json")
        self.assertFalse(omni_det_scan.packet_mr_matches(other, "136",
                                                         "73279395"))
        # a full-path --project is not comparable: that half is skipped
        self.assertTrue(omni_det_scan.packet_mr_matches(
            ok, "136", "group%2Fsubgroup%2Frepo"))
        # A5c: non-numeric --mr coerces to a clean False, never a traceback
        for project in ("73279395", "group%2Fsubgroup%2Frepo"):
            self.assertFalse(omni_det_scan.packet_mr_matches(
                ok, "abc", project))

    def test_severity_mapping_table(self):
        for raw, mapped in (("critical", "critical"),
                            ("high", "important"),
                            ("medium", "minor"),
                            ("low", "minor")):
            self.assertEqual(omni_det_scan.map_severity(raw), mapped, raw)


class DetScanRenderTests(unittest.TestCase):

    def setUp(self):
        _require_det_scan()

    def test_thread_body_exact_template(self):
        body = omni_det_scan.thread_body(FINDING)
        self.assertTrue(body.startswith("det-scan:"))
        self.assertIn("det-scan: [gitleaks] aws-access-key — src/app.py:42",
                      body)
        # mapped marker AND raw packet severity, on the same line
        self.assertIn("**Critical** — scanner severity critical · class "
                      "secret", body)
        # preview verbatim, never re-redacted or altered
        self.assertIn(FINDING["preview_redacted"], body)
        self.assertIn("**Disposition: needs_judgment**", body)
        self.assertIn("never auto-resolved", body)

    def test_thread_body_no_priors_channel_markers(self):
        # A4: "Confidence: "/"Found by: " would flip is_omniforge_note's
        # conjunction and route det-scan threads into the priors channel
        for raw in omni_det_scan.SEVERITIES:
            body = omni_det_scan.thread_body(dict(FINDING, severity=raw))
            self.assertNotIn("Confidence: ", body, raw)
            self.assertNotIn("Found by: ", body, raw)

    def test_summary_body_disclosures(self):
        pkt = packet("detfilter_packet_ok.json")
        body = omni_det_scan.summary_body(pkt, "136", 2, [])
        self.assertIn("det-scan: scanner evidence — MR !136 @ bbb222", body)
        self.assertIn("**Scan status:** ok — ", body)
        # one line per tool: name, version, status, duration
        self.assertIn("- gitleaks 8.18.0 — ok (4.2s)", body)
        self.assertIn("- opengrep 1.2.3 — ok (11.5s)", body)
        self.assertIn("**Findings:** 2 total · 2 anchored (threads below) · "
                      "0 unanchored (see below)", body)
        self.assertIn("**Capped:** False · overflow not adjudicated: 0",
                      body)
        self.assertIn("**Redaction:** REDACTED:<type>:<hash8>", body)
        # the needs_judgment explainer (scanner = evidence, model = verdict)
        self.assertIn("**How to read this:**", body)
        self.assertIn("`needs_judgment`", body)
        self.assertIn("never auto-resolved", body)

    def test_summary_body_unanchored_section_conditional(self):
        pkt = packet("detfilter_packet_ok.json")
        # U == 0: no Unanchored section at all
        anchored = omni_det_scan.summary_body(pkt, "136", 2, [])
        self.assertNotIn("Unanchored evidence", anchored)
        # U > 0: heading + one line per unanchored finding, never dropped
        unanchored = list(pkt["findings"])
        body = omni_det_scan.summary_body(pkt, "136", 0, unanchored)
        self.assertIn("### Unanchored evidence (no anchorable diff line — "
                      "adjudicate from the file)", body)
        for f in unanchored:
            self.assertIn("- [%s] %s — %s:%d — severity %s — %s" % (
                f["tool"], f["rule_id"], f["file"], f["line"],
                f["severity"], f["preview_redacted"]), body)

    def test_summary_body_no_severity_markers(self):
        # FR-6: the summary must never pollute extract_severity — no
        # **Critical/**Important/**Minor tokens in ANY render (raw severity
        # words inside the unanchored list are fine)
        pkt = packet("detfilter_packet_ok.json")
        for body in (omni_det_scan.summary_body(pkt, "136", 2, []),
                     omni_det_scan.summary_body(pkt, "136", 0,
                                                list(pkt["findings"]))):
            for marker in ("**Critical", "**Important", "**Minor"):
                self.assertNotIn(marker, body, marker)


class DetScanEngineTests(unittest.TestCase):

    def setUp(self):
        _require_det_scan()
        self.transport = FakeTransport()
        patcher = mock.patch.object(omni_glab_api, "request",
                                    self.transport.request)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ── helpers ────────────────────────────────────────────────────────

    def seed_diffs(self):
        """Serve both diffs pages; returns (page1, page2) items."""
        page1 = diffs_page("detfilter_mr_diffs_page1.json")
        page2 = diffs_page("detfilter_mr_diffs_page2.json")
        self.transport.gets[DIFFS_PAGE1] = page1
        self.transport.gets[DIFFS_PAGE2] = page2
        return page1, page2

    def anchor_map(self):
        return omni_det_scan.fetch_anchor_map(
            PROJECT, MR, "tok-test", None, 3, 0)

    def notes_gets(self):
        return [c[1] for c in self.transport.calls
                if c[0] == "GET" and "/notes?per_page=100" in c[1]]

    def diffs_gets(self):
        return [c[1] for c in self.transport.calls
                if c[0] == "GET" and "/diffs?per_page=100" in c[1]]

    # ── FR-3 dedup guard (mirrors newer_summary_note exactly) ──────────

    def test_dedup_fresh_note_detected(self):
        # since = epoch-100: the seeded note's created_at is strictly newer
        self.transport.gets[NOTES_PAGE1] = [DET_SCAN_NOTE]
        note = omni_det_scan.newer_det_scan_note(
            PROJECT, MR, DET_SCAN_EPOCH - 100, "tok-test", None, 3, 0)
        self.assertIs(note, DET_SCAN_NOTE)
        # pagination: 100 filler notes on page 1 push the det-scan note onto
        # page 2 — the guard still finds it AND both pages are GET'd (FR-3:
        # a busy MR cannot hide a prior note on page 2)
        self.transport.calls = []
        self.transport.gets[NOTES_PAGE1] = [
            {"body": "human note %d" % i,
             "created_at": "2026-09-02T09:00:00Z"} for i in range(100)]
        self.transport.gets[NOTES_PAGE2] = [DET_SCAN_NOTE]
        note = omni_det_scan.newer_det_scan_note(
            PROJECT, MR, DET_SCAN_EPOCH - 100, "tok-test", None, 3, 0)
        self.assertIs(note, DET_SCAN_NOTE)
        self.assertEqual(self.notes_gets(), [NOTES_PAGE1, NOTES_PAGE2])

    def test_dedup_old_note_ignored(self):
        # since = epoch+100: a pre-round note is NOT newer — round-2+
        # reviews with a fresh packet proceed (FR-3)
        self.transport.gets[NOTES_PAGE1] = [DET_SCAN_NOTE]
        self.assertIsNone(omni_det_scan.newer_det_scan_note(
            PROJECT, MR, DET_SCAN_EPOCH + 100, "tok-test", None, 3, 0))

    def test_dedup_midbody_prefix_ignored(self):
        # a body merely CONTAINING the prefix never trips the guard...
        self.transport.gets[NOTES_PAGE1] = [
            {"body": "the det-scan: prefix marks evidence",
             "created_at": "2026-09-02T10:00:00Z"}]
        self.assertIsNone(omni_det_scan.newer_det_scan_note(
            PROJECT, MR, DET_SCAN_EPOCH - 100, "tok-test", None, 3, 0))
        # ...while leading whitespace IS tolerated (FR-3 lstrip semantics)
        indented = {"body": "  det-scan: indented",
                    "created_at": "2026-09-02T10:00:00Z"}
        self.transport.gets[NOTES_PAGE1] = [indented]
        self.assertIs(omni_det_scan.newer_det_scan_note(
            PROJECT, MR, DET_SCAN_EPOCH - 100, "tok-test", None, 3, 0),
            indented)

    # ── FR-4 head-sha guard ────────────────────────────────────────────

    def test_head_sha_matches_case_insensitive(self):
        self.assertTrue(omni_det_scan.head_sha_matches("BBB222", "bbb222"))
        self.assertTrue(omni_det_scan.head_sha_matches("bbb222", "BBB222"))
        self.assertFalse(omni_det_scan.head_sha_matches("deadbee", "bbb222"))
        # empty on either side never matches — never anchor on an unknown
        # head
        for empty in ("", None):
            self.assertFalse(omni_det_scan.head_sha_matches(empty, "bbb222"))
            self.assertFalse(omni_det_scan.head_sha_matches("bbb222", empty))

    # ── FR-8 anchor chain (get_all -> assemble_diff -> parse_diff_line_map)

    def test_fetch_anchor_map_two_pages(self):
        page1, page2 = self.seed_diffs()
        self.assertEqual(len(page1), 100)   # get_all turns at EXACTLY 100
        anchor_map = self.anchor_map()
        self.assertEqual(anchor_map["src/app.py"]["added_lines"], [42])
        self.assertEqual(anchor_map["src/util.py"]["added_lines"], [7])
        self.assertEqual(anchor_map["src/extra.py"]["added_lines"], [1])
        # 99 fillers + app.py + util.py + extra.py — every item made it in
        self.assertEqual(len(anchor_map), 102)
        self.assertEqual(self.diffs_gets(), [DIFFS_PAGE1, DIFFS_PAGE2])
        # a SHORT page (< 100 items) terminates pagination after ONE GET
        self.transport.calls = []
        self.transport.gets = {DIFFS_PAGE1: page2}    # 2 items: short page
        m = self.anchor_map()
        self.assertEqual(self.diffs_gets(), [DIFFS_PAGE1])
        self.assertEqual(m["src/util.py"]["added_lines"], [7])

    def test_split_findings_anchorable_vs_unanchored(self):
        self.seed_diffs()
        pkt = packet("detfilter_packet_unanchored.json")
        anchored, unanchored = omni_det_scan.split_findings(
            pkt, self.anchor_map())
        # packet order preserved in BOTH lists (the fixture interleaves):
        # findings[0] app.py:42 and findings[2] util.py:7 anchor — across
        # the pagination boundary — while findings[1] src/ghost.py:3 (wrong
        # file) and findings[3] src/app.py:999 (wrong line) do not
        self.assertEqual([f["rule_id"] for f in anchored],
                         ["aws-access-key", "sql-injection"])
        self.assertEqual([f["rule_id"] for f in unanchored],
                         ["hardcoded-url", "CVE-2026-1234"])
        # partition: every finding lands in exactly one list — the
        # unanchorable are returned for disclosure, never silently dropped
        self.assertEqual(len(anchored) + len(unanchored),
                         len(pkt["findings"]))


NOTES_BASE_PATH = MR_PATH + "/notes"
DISCUSSIONS_PATH = MR_PATH + "/discussions"


class DetScanCliTests(unittest.TestCase):
    """T3 CLI contract: the pinned guard order, posting, dry-run, and the
    one-JSON-line receipt (FR-2/FR-5/FR-7..FR-11, A5a/b/d). main() runs
    under the raw_poster convention; packet fixtures are COPIED into a
    per-test tmp dir (mtime control via os.utime; head-sha variants edited
    in-memory) so nothing ever writes into the repo."""

    def setUp(self):
        _require_det_scan()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.transport = FakeTransport()
        patcher = mock.patch.object(omni_glab_api, "request",
                                    self.transport.request)
        patcher.start()
        self.addCleanup(patcher.stop)
        # default seeding: MR metadata GET, empty notes page 1, both diffs
        # pages (page 1 carries src/app.py:42, page 2 src/util.py:7 —
        # anchors cross the pagination boundary)
        self.transport.gets[MR_PATH] = DIFF_REFS
        self.transport.gets[NOTES_PAGE1] = []
        self.transport.gets[DIFFS_PAGE1] = diffs_page(
            "detfilter_mr_diffs_page1.json")
        self.transport.gets[DIFFS_PAGE2] = diffs_page(
            "detfilter_mr_diffs_page2.json")
        self._packet_seq = 0

    # ── helpers (raw_poster mirror) ────────────────────────────────────

    def tmp_packet(self, source, mutate=None):
        """Copy a packet fixture (name or already-parsed dict) into self.tmp;
        mutate(obj) may edit it in place first. tmp copies keep os.utime
        mtime control and in-memory variants OUT of the repo."""
        if isinstance(source, dict):
            obj = source
        else:
            with open(os.path.join(DETFILTER, source),
                      encoding="utf-8") as fh:
                obj = json.load(fh)
        if mutate is not None:
            mutate(obj)
        self._packet_seq += 1
        path = os.path.join(self.tmp, "packet-%d.json" % self._packet_seq)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return path

    def tmp_copy(self, name):
        """Byte-copy a fixture into self.tmp (for the non-JSON fixtures)."""
        self._packet_seq += 1
        dst = os.path.join(self.tmp, "copy-%d" % self._packet_seq)
        shutil.copyfile(os.path.join(DETFILTER, name), dst)
        return dst

    def raw_det(self, *argv, token="tok-test"):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, run_env(token), clear=True), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                code = omni_det_scan.main(list(argv))
            except SystemExit as e:              # argparse usage errors (2)
                code = e.code
        return types.SimpleNamespace(returncode=code, stdout=out.getvalue(),
                                     stderr=err.getvalue())

    def run_det(self, *extra, packet=None, name="detfilter_packet_ok.json",
                token="tok-test"):
        """main() with the default argv prefix (tmp-copied fixture + the
        plan's fixed flags); `packet` overrides with a pre-made path."""
        ppath = packet if packet is not None else self.tmp_packet(name)
        argv = ["--packet", ppath, "--project", PROJECT, "--mr", MR,
                "--backoff-base", "0"] + list(extra)
        return self.raw_det(*argv, token=token)

    def receipt(self, result):
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        return json.loads(lines[-1])

    def posts(self, path):
        return [c for c in self.transport.calls
                if c[0] == "POST" and c[1] == path]

    def post_posts(self):
        return [c for c in self.transport.calls if c[0] == "POST"]

    def form_value(self, call, key):
        for k, v in call[2]:
            if k == key:
                return v
        return None

    # ── FR-1: validation exits 2, NO stdout ────────────────────────────

    def test_unknown_top_level_field_rejected(self):
        r = self.run_det(name="detfilter_packet_unknown_top.json")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown-top-level-field:extra", r.stderr)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(r.stdout, "")            # exit 2: no receipt

    def test_packet_not_json_and_unreadable_exit2_no_stdout(self):
        for name in ("detfilter_packet_not_json.txt",
                     "detfilter_packet_unreadable.bin"):
            r = self.run_det(packet=self.tmp_copy(name))
            self.assertEqual(r.returncode, 2, name)
            self.assertEqual(r.stdout, "", name)
            self.assertIn("packet-not-json", r.stderr, name)
        # a DIRECTORY as --packet: IsADirectoryError -> packet-unreadable
        r = self.run_det(packet=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("packet-unreadable", r.stderr)

    def test_packet_mr_mismatch_exit2(self):
        r = self.run_det(name="detfilter_packet_other_mr.json")
        self.assertEqual(r.returncode, 2)
        self.assertIn("packet-mr-mismatch", r.stderr)
        self.assertEqual(r.stdout, "")
        # A5c: non-numeric --mr coerces to a clean mismatch — no traceback
        r = self.run_det("--mr", "abc")
        self.assertEqual(r.returncode, 2)
        self.assertIn("packet-mr-mismatch", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    # ── FR-2: staleness guard (local, before any network) ──────────────

    def test_stale_packet_refuses(self):
        r = self.run_det("--since", "1000", "--packet-epoch", "500")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.post_posts(), [])
        receipt = self.receipt(r)
        self.assertEqual(receipt["action"], "skipped")
        self.assertEqual(receipt["skip_reason"], "stale-packet")

    def test_packet_epoch_overrides_mtime(self):
        ppath = self.tmp_packet()
        # old mtime + --packet-epoch >= since: the EXPLICIT epoch wins
        os.utime(ppath, (1, 1))
        r = self.run_det("--since", "500", "--packet-epoch", "600",
                         packet=ppath)
        self.assertEqual(r.returncode, 0)
        # fresh mtime + --packet-epoch < since: the EXPLICIT epoch still wins
        now = time.time()
        os.utime(ppath, (now, now))
        r = self.run_det("--since", "500", "--packet-epoch", "400",
                         packet=ppath)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.receipt(r)["skip_reason"], "stale-packet")

    def test_epoch_wiring(self):
        ppath = self.tmp_packet()
        engine_epoch = time.time()
        # (a) engine epoch present (--since) + old packet mtime -> stale
        os.utime(ppath, (engine_epoch - 50,) * 2)
        r = self.run_det("--since", str(engine_epoch), packet=ppath)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.receipt(r)["skip_reason"], "stale-packet")
        # (b) mtime >= engine epoch -> proceeds and posts
        os.utime(ppath, (engine_epoch + 50,) * 2)
        r = self.run_det("--since", str(engine_epoch), packet=ppath)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(self.posts(NOTES_BASE_PATH))
        # (c) epoch absent -> --since 0 default -> guard dormant -> proceeds
        r = self.run_det(packet=ppath)
        self.assertEqual(r.returncode, 0)

    # ── FR-5: scan-status handling ─────────────────────────────────────

    def test_scan_skipped_is_benign_noop(self):
        r = self.run_det(name="detfilter_packet_skipped.json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.post_posts(), [])
        receipt = self.receipt(r)
        self.assertEqual(receipt["action"], "skipped")
        self.assertEqual(receipt["skip_reason"],
                         "scan-skipped:no pipeline token")

    def test_skipped_with_mismatched_head_zero_calls(self):
        # the skipped check runs BEFORE any network guard — a mismatched
        # head never even fetches the MR (R10 ordering)
        pkt = packet("detfilter_packet_skipped.json")
        pkt["mr"]["head_sha"] = "deadbee"         # != the seeded bbb222
        r = self.run_det(packet=self.tmp_packet(pkt))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.transport.calls, [])

    def test_incomplete_posts_with_disclosure(self):
        r = self.run_det(name="detfilter_packet_incomplete.json")
        self.assertEqual(r.returncode, 0)
        notes_posts = self.posts(NOTES_BASE_PATH)
        self.assertEqual(len(notes_posts), 1)
        body = self.form_value(notes_posts[0], "body")
        self.assertIn("incomplete", body)
        self.assertIn("trivy", body)
        self.assertIn("timeout", body)
        self.assertIn("**Capped:** True · overflow not adjudicated: 3", body)
        self.assertIn("**How to read this:**", body)

    # ── FR-4 / FR-3: the two network guards ────────────────────────────

    def test_head_sha_mismatch_refuses(self):
        r = self.run_det(name="detfilter_packet_head_mismatch.json")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.post_posts(), [])
        receipt = self.receipt(r)
        self.assertEqual(receipt["action"], "skipped")
        self.assertEqual(receipt["skip_reason"], "head-sha-mismatch")
        # stderr names both shas (8-char prefixes)
        self.assertIn("deadbee", r.stderr)
        self.assertIn("bbb222", r.stderr)

    def test_dedup_refuses_on_fresh_note(self):
        self.transport.gets[NOTES_PAGE1] = [DET_SCAN_NOTE]
        r = self.run_det("--since", str(DET_SCAN_EPOCH - 100))
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.post_posts(), [])
        receipt = self.receipt(r)
        self.assertEqual(receipt["action"], "skipped")
        self.assertEqual(receipt["skip_reason"], "det-scan-already-posted")

    def test_old_det_scan_note_proceeds(self):
        self.transport.gets[NOTES_PAGE1] = [DET_SCAN_NOTE]
        r = self.run_det("--since", str(DET_SCAN_EPOCH + 100))
        self.assertEqual(r.returncode, 0)
        self.assertTrue(self.posts(NOTES_BASE_PATH))

    def test_force_overrides_dedup(self):
        self.transport.gets[NOTES_PAGE1] = [DET_SCAN_NOTE]
        r = self.run_det("--since", str(DET_SCAN_EPOCH - 100), "--force")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(self.posts(NOTES_BASE_PATH))

    # ── FR-10: dry-run + receipt contract ──────────────────────────────

    def test_dry_run_zero_transport_calls(self):
        r = self.run_det("--dry-run", token=None)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.transport.calls, [])
        receipt = self.receipt(r)
        self.assertTrue(receipt["dry_run"])
        self.assertEqual(receipt["threads"], 0)
        self.assertEqual(receipt["unanchored"], 0)
        self.assertEqual(receipt["planned_findings"], 2)

    def test_dry_run_receipt_and_placeholder_plan_lines(self):
        r = self.run_det("--dry-run")
        # chunk on the "DRY-RUN: " prefix, never on newlines (dry_line's
        # contract — bodies contain newlines); the LAST chunk also carries
        # the receipt after its trailing newline
        chunks = r.stdout.split("DRY-RUN: ")[1:]
        notes = [c for c in chunks
                 if c.startswith("POST %s " % NOTES_BASE_PATH)]
        discs = [c for c in chunks
                 if c.startswith("POST %s " % DISCUSSIONS_PATH)]
        self.assertEqual(len(notes), 1)
        self.assertEqual(len(discs), 2)
        # A5a/b: the sibling PLACEHOLDER_REFS tokens, substring-matched
        # (shlex.quote wraps them in quotes)
        for chunk in discs:
            for placeholder in ("<base_sha>", "<start_sha>", "<head_sha>"):
                self.assertIn(placeholder, chunk)
            self.assertIn("position[position_type]=text", chunk)
        # per-finding anchors in packet order (app.py:42 then util.py:7 —
        # anchorability is unknown in dry-run, so ALL findings are planned)
        self.assertIn("position[new_path]=src/app.py", discs[0])
        self.assertIn("position[new_line]=42", discs[0])
        self.assertIn("position[new_path]=src/util.py", discs[1])
        self.assertIn("position[new_line]=7", discs[1])
        receipt = self.receipt(r)
        self.assertTrue(receipt["dry_run"])
        self.assertEqual(receipt["threads"], 0)
        self.assertEqual(receipt["planned_findings"], 2)

    def test_missing_token_exit2_and_stale_no_token_exits3(self):
        # real run without a token: exit 2 naming the sibling fix line
        r = self.run_det(token=None)
        self.assertEqual(r.returncode, 2)
        self.assertIn("glab auth status", r.stderr)
        self.assertEqual(self.transport.calls, [])
        # A5d: the token check runs AFTER all local guards — a stale packet
        # with no token exits 3 (stale-packet), not 2
        r = self.run_det("--packet-epoch", "1", "--since", "1000",
                         token=None)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.receipt(r)["skip_reason"], "stale-packet")

    def test_exit3_receipts_distinguishable(self):
        stale = self.receipt(
            self.run_det("--since", "1000", "--packet-epoch", "500"))
        head = self.receipt(
            self.run_det(name="detfilter_packet_head_mismatch.json"))
        self.transport.gets[NOTES_PAGE1] = [DET_SCAN_NOTE]
        dedup = self.receipt(
            self.run_det("--since", str(DET_SCAN_EPOCH - 100)))
        for receipt in (stale, head, dedup):
            self.assertEqual(receipt["action"], "skipped")
        self.assertEqual(len({stale["skip_reason"], head["skip_reason"],
                              dedup["skip_reason"]}), 3)
        self.assertEqual(stale["skip_reason"], "stale-packet")
        self.assertEqual(head["skip_reason"], "head-sha-mismatch")
        self.assertEqual(dedup["skip_reason"], "det-scan-already-posted")

    # ── FR-9: side-effect discipline (SC-4) ────────────────────────────

    def test_only_get_post_methods_and_no_label_paths(self):
        r = self.run_det()
        self.assertEqual(r.returncode, 0)
        for method, path, form in self.transport.calls:
            self.assertIn(method, ("GET", "POST"), (method, path))
            self.assertNotIn("label", path, path)
            self.assertIsNone(
                re.search(r"/discussions/[^/]+/notes", path), path)

    # ── FR-7: posting failures keep prior artifacts ────────────────────

    def test_mid_post_failure_exit1(self):
        # thread 1 succeeds, thread 2 exhausts its retries
        self.transport.script(DISCUSSIONS_PATH,
                              [(200, {}), 500, 500, 500])
        r = self.run_det()
        self.assertEqual(r.returncode, 1)
        receipt = self.receipt(r)
        self.assertGreaterEqual(receipt["failures"], 1)
        self.assertEqual(receipt["threads"], 1)
        self.assertTrue(self.posts(NOTES_BASE_PATH))   # summary stays up

    def test_summary_post_failure_exit1(self):
        # summary-first: a failed summary POST aborts before ANY thread
        self.transport.script(NOTES_BASE_PATH, [500, 500, 500])
        r = self.run_det()
        self.assertEqual(r.returncode, 1)
        receipt = self.receipt(r)
        self.assertFalse(receipt["posted_summary"])
        self.assertEqual(receipt["threads"], 0)
        self.assertEqual(self.posts(DISCUSSIONS_PATH), [])
        self.assertGreaterEqual(receipt["failures"], 1)

    # ── FR-8: unanchored disclosure ────────────────────────────────────

    def test_unanchored_disclosed_no_thread_counts(self):
        r = self.run_det(name="detfilter_packet_unanchored.json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(self.posts(DISCUSSIONS_PATH)), 2)
        body = self.form_value(self.posts(NOTES_BASE_PATH)[0], "body")
        self.assertIn("### Unanchored evidence", body)
        self.assertIn("src/ghost.py:3", body)
        self.assertIn("src/app.py:999", body)
        receipt = self.receipt(r)
        self.assertEqual(receipt["threads"], 2)
        self.assertEqual(receipt["unanchored"], 2)
        self.assertEqual(receipt["findings_total"], 4)
        self.assertEqual(receipt["findings_total"],
                         receipt["threads"] + receipt["unanchored"])

    # ── FR-7/SC-3: the E2E path — packet in, MR artifacts out ──────────

    def test_end_to_end_packet_to_mr_threads(self):
        r = self.run_det()
        self.assertEqual(r.returncode, 0)
        calls = self.transport.calls

        # exactly ONE summary note POST, det-scan:-prefixed
        notes_posts = self.posts(NOTES_BASE_PATH)
        self.assertEqual(len(notes_posts), 1)
        self.assertTrue(self.form_value(notes_posts[0], "body")
                        .startswith("det-scan:"))

        # exactly TWO discussion POSTs in packet order, anchored across the
        # pagination boundary (app.py:42 lives on page 1, util.py:7 on page 2)
        disc_posts = self.posts(DISCUSSIONS_PATH)
        self.assertEqual(len(disc_posts), 2)
        expected = [("src/app.py", "42"), ("src/util.py", "7")]
        for call, (new_path, new_line) in zip(disc_posts, expected):
            form = dict(call[2])
            body = form["body"]
            self.assertTrue(body.startswith("det-scan:"), body[:40])
            self.assertEqual(form["position[position_type]"], "text")
            self.assertEqual(form["position[base_sha]"], "aaa111")
            self.assertEqual(form["position[start_sha]"], "ccc333")
            self.assertEqual(form["position[head_sha]"], "bbb222")
            self.assertEqual(form["position[new_path]"], new_path)
            self.assertEqual(form["position[new_line]"], new_line)
            self.assertNotIn("position[old_line]", form)   # no old-side locus
            self.assertNotIn("position[line_range]", form)
            self.assertIn("**Disposition: needs_judgment**", body)
        first = dict(disc_posts[0][2])["body"]
        second = dict(disc_posts[1][2])["body"]
        # mapped marker AND raw packet severity (FR-6), preview verbatim
        self.assertIn("**Critical** — scanner severity critical", first)
        self.assertIn("AWS Access Key ID=REDACTED:secret:ab12cd34", first)
        self.assertIn("**Minor** — scanner severity medium", second)
        self.assertIn('query = f"SELECT * FROM t WHERE '
                      'id={REDACTED:secret:ef56ab78}"', second)

        # exactly ONE MR metadata GET (N+1 discipline) + the 2 diffs GETs
        meta_gets = [c for c in calls if c[0] == "GET" and c[1] == MR_PATH]
        self.assertEqual(len(meta_gets), 1)
        diffs_gets = [c for c in calls
                      if c[0] == "GET" and "/diffs?per_page=100" in c[1]]
        self.assertEqual(len(diffs_gets), 2)

        # summary posted BEFORE the first thread (call-order)
        self.assertLess(calls.index(notes_posts[0]),
                        calls.index(disc_posts[0]))

        # the receipt: full 13-key shape, executed (not planned) counts
        receipt = self.receipt(r)
        self.assertEqual(set(receipt), {
            "action", "skip_reason", "posted_summary", "threads",
            "unanchored", "planned_findings", "findings_total",
            "scan_status", "packet_epoch", "head_sha", "dry_run",
            "failures", "elapsed_ms"})
        self.assertEqual(receipt["action"], "posted")
        self.assertTrue(receipt["posted_summary"])
        self.assertEqual(receipt["threads"], 2)
        self.assertEqual(receipt["unanchored"], 0)
        self.assertEqual(receipt["findings_total"], 2)
        self.assertEqual(receipt["planned_findings"], 0)
        self.assertEqual(receipt["scan_status"], "ok")
        self.assertEqual(receipt["head_sha"], "bbb222")
        self.assertFalse(receipt["dry_run"])
        self.assertEqual(receipt["failures"], 0)
        self.assertIsNone(receipt["skip_reason"])


if __name__ == "__main__":
    unittest.main()
