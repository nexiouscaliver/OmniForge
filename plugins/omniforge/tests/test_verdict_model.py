"""Rule-table unit tests for the omnicheck verification model v2 (P1).

Pins the vocabulary decision recorded in
skills/omnicheck-gitlab/references/verdict-model.md:
the closed disposition set, severity/kind extraction from real posted-body
conventions (OmniForge markers, regenloop-ship blocking/non-blocking/note,
structured severity JSON), artifact/command filtering, valid-consent
detection, and the deterministic gate truth table (CLEAN / READY_WITH_NOTES /
BLOCKED). No prose parsing beyond the marker tables; no network; no LLM.
"""

import importlib.util
import os
import unittest

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                       "skills", "omnicheck-gitlab", "scripts"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "omni_verdict", os.path.join(SCRIPTS, "omni_verdict.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


omni_verdict = _load()


class VocabularyTests(unittest.TestCase):
    def test_dispositions_closed_set(self):
        self.assertEqual(
            omni_verdict.DISPOSITIONS,
            ("fixed", "not_fixed", "obsolete", "deferred", "declined",
             "decision", "artifact"))

    def test_severities_and_kinds_closed_sets(self):
        self.assertEqual(omni_verdict.SEVERITIES,
                         ("critical", "important", "minor"))
        self.assertEqual(omni_verdict.KINDS,
                         ("code", "test", "docs", "process", "decision"))
        self.assertEqual(omni_verdict.GATE_OUTCOMES,
                         ("CLEAN", "READY_WITH_NOTES", "BLOCKED"))


class ArtifactFilterTests(unittest.TestCase):
    def test_omniforge_report_post_is_artifact(self):
        body = ("## OmniForge\n\n**Verdict:** REQUEST_CHANGES\n\n### Overview\n"
                "This MR fixes a real and well-diagnosed incident class.")
        self.assertTrue(omni_verdict.is_artifact(body))

    def test_review_started_note_is_artifact(self):
        body = ("🔄 **OmniForge review started** (trigger: comment by "
                "@shahilkadia) — typically 15–45 minutes.")
        self.assertTrue(omni_verdict.is_artifact(body))

    def test_fix_brief_is_artifact(self):
        body = ("## OmniForge fix brief — paste this into your coding agent\n"
                "Everything below the second divider is self-contained.")
        self.assertTrue(omni_verdict.is_artifact(body))

    def test_disposition_summary_is_artifact(self):
        body = "## Disposition summary — 17 findings\n\nTwo commits, no history rewritten."
        self.assertTrue(omni_verdict.is_artifact(body))

    def test_regenloop_round_summary_is_artifact(self):
        # loop !59: the round-1 summary post was classified as a finding by
        # the old model — it must never be one
        body = ("<!-- regenloop-ship:review:round-1 -->\n\n### Review round 1\n\n"
                "**2 blocking, 8 non-blocking** findings …")
        self.assertTrue(omni_verdict.is_artifact(body))

    def test_trigger_commands_are_artifacts(self):
        for cmd in ("/omnireview", "/omniforge", "/omnicheck"):
            self.assertTrue(omni_verdict.is_artifact(cmd), cmd)
            self.assertTrue(omni_verdict.is_artifact("  " + cmd + "  \n"), cmd)

    def test_finding_thread_is_not_artifact(self):
        body = ("**Important** — New per-pass release on the trial-expiry lock "
                "is scoped by a container-colliding `worker-<pid>` identity.")
        self.assertFalse(omni_verdict.is_artifact(body))

    def test_regenloop_finding_with_marker_comment_is_not_artifact(self):
        # per-finding threads carry the same HTML marker comment as the round
        # summary — only the round-SUMMARY shape is an artifact
        body = ("<!-- regenloop-ship:review:round-1:f-815c9be8abfb --> "
                "**blocking** — The hook bounds only the admission-queue depth.")
        self.assertFalse(omni_verdict.is_artifact(body))

    def test_nudge_reply_is_never_consent(self):
        replies = [{"body": "This concern appears unaddressed in the latest "
                            "changes.\n\ndocs/gate-tuning.md still carries "
                            "both rows."}]
        self.assertIsNone(omni_verdict.extract_consent(replies))


class SeverityTests(unittest.TestCase):
    def test_omniforge_markers(self):
        for marker, want in (("**Critical**", "critical"),
                             ("**Important**", "important"),
                             ("**Minor**", "minor")):
            body = f"{marker} — Something about the code\n\n**What:** detail."
            self.assertEqual(omni_verdict.extract_severity(body), want, marker)

    def test_marker_prefix_variants(self):
        self.assertEqual(
            omni_verdict.extract_severity("**Critical-CONTESTED gate bypass** — …"),
            "critical")

    def test_structured_severity_json(self):
        body = '{"severity": "important", "title": "coarse trigger"}'
        self.assertEqual(omni_verdict.extract_severity(body), "important")

    def test_regenloop_blocking_convention(self):
        self.assertEqual(
            omni_verdict.extract_severity("**blocking** — §9's target fetch is unguarded."),
            "important")
        self.assertEqual(
            omni_verdict.extract_severity("**non-blocking** — `AHEAD` default …"),
            "minor")
        self.assertEqual(
            omni_verdict.extract_severity("**note** — \"Pinned by 8 ne…"),
            "minor")

    def test_security_paren_variants(self):
        self.assertEqual(
            omni_verdict.extract_severity("**non-blocking (security)** — Three …"),
            "minor")
        self.assertEqual(
            omni_verdict.extract_severity("**note (security)** — Thread …"),
            "minor")

    def test_unmarked_defaults_to_minor(self):
        self.assertEqual(omni_verdict.extract_severity("Missing null check on user input"),
                         "minor")


class KindTests(unittest.TestCase):
    def test_test_paths(self):
        for path in ("tests/test_guard.py", "test_widget.py",
                     "pkg/test_helper.py", "src/app.test.ts", "spec/model.spec.js"):
            self.assertEqual(omni_verdict.extract_kind("check the fixture", path),
                             "test", path)

    def test_docs_paths(self):
        for path in ("docs/gate-tuning.md", "README.rst", "notes.txt"):
            self.assertEqual(omni_verdict.extract_kind("two contradictory rows", path),
                             "docs", path)

    def test_process_concerns(self):
        for body in ("**Important** — Head pipeline is red (3 failures) and has hung for >24h",
                     "Four separable concerns bundled into one MR, against the one-concern-per-PR rule",
                     "Core commit is 1523 insertions across nine files doing four logically distinct things",
                     "0/2 required approvals were recorded prior to this one",
                     "No reviewer assigned"):
            self.assertEqual(omni_verdict.extract_kind(body, None), "process", body[:40])

    def test_decision_concerns(self):
        for body in ("Flagged to the operator for an explicit call rather than decided quietly",
                     "Maintainer decision needed on the flag-rollout ordering"):
            self.assertEqual(omni_verdict.extract_kind(body, None), "decision", body[:40])

    def test_code_default(self):
        self.assertEqual(
            omni_verdict.extract_kind("Missing null check on user input", "src/auth.py"),
            "code")

    def test_code_referenced_in_docs_body_is_code_when_anchor_is_code(self):
        # anchor path wins: a .md-mentioning body on a .py anchor is code
        self.assertEqual(
            omni_verdict.extract_kind("see docs/gate-tuning.md for the default",
                                      "regenloop_guard.py"),
            "code")


class ConsentTests(unittest.TestCase):
    def test_skipped_reply_is_declined(self):
        replies = [{"body": "**SKIPPED** — the mechanism is real and correctly "
                            "described, but it is a pre-existing, already-tested path."}]
        disp = omni_verdict.extract_consent(replies)
        self.assertEqual(disp[0], "declined")

    def test_acknowledged_reply_is_declined(self):
        replies = [{"body": "**Acknowledged, not rewriting.** The branch history stays."}]
        disp = omni_verdict.extract_consent(replies)
        self.assertEqual(disp[0], "declined")

    def test_answered_reply_is_declined(self):
        replies = [{"body": "**Answered, not changed here.** SAST/SEC tooling owns it."}]
        disp = omni_verdict.extract_consent(replies)
        self.assertEqual(disp[0], "declined")

    def test_deferral_reply_is_deferred(self):
        replies = [{"body": "**Deferred** to the backend MR (!1210) — the API "
                            "lands there first."}]
        disp = omni_verdict.extract_consent(replies)
        self.assertEqual(disp[0], "deferred")

    def test_explicit_call_reply_is_decision(self):
        replies = [{"body": "**SKIPPED** — flagged to the operator for an "
                            "explicit call rather than decided quietly."}]
        disp = omni_verdict.extract_consent(replies)
        self.assertEqual(disp[0], "decision")

    def test_no_reply_no_consent(self):
        self.assertIsNone(omni_verdict.extract_consent([]))
        self.assertIsNone(omni_verdict.extract_consent(
            [{"body": "Looks reasonable, will check."}]))


def thread(tid, body, resolved=None, resolvable=True, replies=None,
           file_path=None):
    return {
        "id": tid,
        "resolvable": resolvable,
        "resolved": resolved,
        "type": "inline" if file_path else "general",
        "file_path": file_path,
        "line_number": 10,
        "body": body,
        "author": "reviewer",
        "replies": replies or [],
    }


class ClassifyThreadTests(unittest.TestCase):
    def test_resolved_thread_is_fixed(self):
        rec = omni_verdict.classify_thread(
            thread("a1", "**Important** — race on the claim path",
                   resolved=True, file_path="db/claim.py"),
            verification=None)
        self.assertEqual(rec["disposition"], "fixed")

    def test_resolved_with_deferral_disclosure_is_deferred(self):
        # cleo !1172's H4: resolved-by-deferral must not read as fixed
        rec = omni_verdict.classify_thread(
            thread("a2", "**Critical** — candidate-domain unreachable",
                   resolved=True, file_path="src/domain.py",
                   replies=[{"body": "Deferred to the backend MR (!1210) — "
                                     "the API lands there first."}]),
            verification=None)
        self.assertEqual(rec["disposition"], "deferred")

    def test_open_thread_without_consent_is_not_fixed(self):
        rec = omni_verdict.classify_thread(
            thread("a3", "**Important** — coarse trigger shape",
                   file_path="hooks/trigger.py"),
            verification=None)
        self.assertEqual(rec["disposition"], "not_fixed")

    def test_open_thread_with_consent_is_declined(self):
        rec = omni_verdict.classify_thread(
            thread("a4", "**Important** — per-pass release scoped by worker id",
                   file_path="schedulers/trial.py",
                   replies=[{"body": "**Acknowledged, kept.** The design "
                                     "works as reviewed."}]),
            verification=None)
        self.assertEqual(rec["disposition"], "declined")

    def test_verification_fixed_wins_for_open_thread(self):
        # silently-applied: open thread the diff proves fixed
        rec = omni_verdict.classify_thread(
            thread("a5", "**Critical** — non-functional pagination",
                   file_path="ui/list.js"),
            verification={"verdict": "SILENTLY_APPLIED"})
        self.assertEqual(rec["disposition"], "fixed")

    def test_artifact_thread_classified_artifact(self):
        rec = omni_verdict.classify_thread(
            thread("a6", "## OmniForge\n\n**Verdict:** REQUEST_CHANGES\n", resolvable=False),
            verification=None)
        self.assertEqual(rec["disposition"], "artifact")

    def test_severity_and_kind_extracted(self):
        rec = omni_verdict.classify_thread(
            thread("a7", "**Critical** — migration filed in wrong directory",
                   file_path="db/migrations/152_geo.sql"),
            verification=None)
        self.assertEqual(rec["severity"], "critical")
        self.assertEqual(rec["kind"], "code")


class GateRuleTableTests(unittest.TestCase):
    def finding(self, disposition="not_fixed", severity="important",
                kind="code", tid="f1"):
        return {"id": tid, "disposition": disposition, "severity": severity,
                "kind": kind}

    def test_empty_is_clean(self):
        self.assertEqual(omni_verdict.gate([])[0], "CLEAN")

    def test_all_fixed_is_clean(self):
        out, blockers, notes = omni_verdict.gate([
            self.finding("fixed", "critical", "code"),
            self.finding("fixed", "minor", "docs", tid="f2")])
        self.assertEqual(out, "CLEAN")
        self.assertEqual(blockers, [])
        self.assertEqual(notes, [])

    def test_not_fixed_critical_code_blocks(self):
        for sev in ("critical", "important"):
            out, blockers, _ = omni_verdict.gate(
                [self.finding("not_fixed", sev, "code")])
            self.assertEqual(out, "BLOCKED", sev)
            self.assertEqual(blockers[0]["severity"], sev)

    def test_not_fixed_minor_code_does_not_block(self):
        out, blockers, notes = omni_verdict.gate(
            [self.finding("not_fixed", "minor", "code")])
        self.assertEqual(out, "READY_WITH_NOTES")
        self.assertEqual(blockers, [])

    def test_not_fixed_important_non_code_never_blocks(self):
        for kind in ("test", "docs", "process", "decision"):
            out, _, _ = omni_verdict.gate(
                [self.finding("not_fixed", "critical", kind, tid="f-" + kind)])
            self.assertEqual(out, "READY_WITH_NOTES", kind)

    def test_disposed_critical_code_does_not_block(self):
        # cleo !1172's deferred Critical: DISPOSED class reports, never gates
        for disp in ("deferred", "declined", "decision"):
            out, blockers, notes = omni_verdict.gate(
                [self.finding(disp, "critical", "code", tid="f-" + disp)])
            self.assertEqual(out, "READY_WITH_NOTES", disp)
            self.assertEqual(blockers, [])
            self.assertTrue(any(n["id"] == "f-" + disp for n in notes), disp)

    def test_obsolete_is_a_note(self):
        out, blockers, notes = omni_verdict.gate(
            [self.finding("obsolete", "critical", "code")])
        self.assertEqual(out, "READY_WITH_NOTES")
        self.assertEqual(blockers, [])

    def test_artifacts_never_reach_the_gate(self):
        out, _, _ = omni_verdict.gate(
            [self.finding("artifact", "critical", "code")])
        self.assertEqual(out, "CLEAN")

    def test_one_blocker_among_fixed_still_blocks(self):
        findings = [
            self.finding("fixed", "critical", "code", tid="ok1"),
            self.finding("not_fixed", "important", "code", tid="bad1"),
            self.finding("declined", "minor", "docs", tid="note1"),
        ]
        out, blockers, notes = omni_verdict.gate(findings)
        self.assertEqual(out, "BLOCKED")
        self.assertEqual([b["id"] for b in blockers], ["bad1"])
        self.assertEqual([n["id"] for n in notes], ["note1"])

    def test_unverified_critical_code_blocks(self):
        # missing verification on an open critical code thread: fail-closed on
        # the blocking class
        rec = omni_verdict.classify_thread(
            thread("u1", "**Critical** — gate bypass via git-config",
                   file_path="ci/gate.py"),
            verification=None)
        self.assertEqual(rec["disposition"], "not_fixed")
        out, _, _ = omni_verdict.gate([rec])
        self.assertEqual(out, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
