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
"""

import importlib.util
import json
import os
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
