"""T1 contract tests for the det-scan v1 evidence consumer's PURE packet
layer (omni_det_scan.py: load/validate/mr-match/severity-map/body render).

The module is loaded IN-PROCESS via importlib (the load_poster_module
convention); this file pins ONLY the pure half — no transport, no CLI:

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
"""

import importlib.util
import json
import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
