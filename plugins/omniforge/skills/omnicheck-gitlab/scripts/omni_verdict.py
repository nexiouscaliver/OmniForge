#!/usr/bin/env python3
"""OmniCheck verification model v2 — deterministic classification + gate.

Single semantic home for "how a finding is verified" (P1). The push sweep
(omni_sweep.py, P2) and the check-mode skill both consume this module's
vocabulary VERBATIM; the vocabulary decision itself lives in
../references/verdict-model.md.

Layers:
  is_artifact()        — artifact/command filtering (never findings)
  extract_severity()   — critical|important|minor from posted-body markers
  extract_kind()       — code|test|docs|process|decision
  extract_consent()    — valid consent (deferred / declined / decision) from
                         human replies; bot boilerplate never counts
  classify_thread()    — one structured finding record per thread
  classify_run()       — full run: findings + artifacts, gate-ready
  gate()               — CLEAN | READY_WITH_NOTES | BLOCKED rule table

Everything here is pure and stdlib-only: no network, no LLM, no repo access.
The verification RESULT (was an open thread's fix present in the diff?) is an
INPUT (`verification`), produced by check-mode analysis or the sweep — this
module never guesses it.
"""

import json
import re
import sys

# ── Vocabulary (closed sets — see references/verdict-model.md) ────────────

DISPOSITIONS = ("fixed", "not_fixed", "obsolete", "deferred", "declined",
                "decision", "artifact")
SEVERITIES = ("critical", "important", "minor")
KINDS = ("code", "test", "docs", "process", "decision")
GATE_OUTCOMES = ("CLEAN", "READY_WITH_NOTES", "BLOCKED")

BLOCKING_SEVERITIES = ("critical", "important")
BLOCKING_KIND = "code"
DISPOSED_DISPOSITIONS = ("deferred", "declined", "decision")

# ── Artifact shapes ────────────────────────────────────────────────────────

TRIGGER_COMMANDS = ("/omnireview", "/omniforge", "/omnicheck")

_ARTIFACT_BODY_MARKERS = (
    "## OmniForge\n",            # review report posts (Verdict: …)
    "## OmniForge fix brief",    # paste-into-your-agent fix briefs
    "## Disposition summary",    # omnifix disposition roll-ups
    "🔄 **OmniForge review started**",
)

_ROUND_SUMMARY_MARKER = re.compile(
    r"<!--\s*regenloop-ship:review:round-\d+\s*-->")
_ROUND_SUMMARY_HEADER = "### Review round"

# Bot reply templates — replies only, never findings, never consent.
NUDGE_REPLY_PREFIX = "This concern appears unaddressed"

_SEVERITY_MARKERS = (
    ("critical", re.compile(r"\*\*Critical\b")),
    ("important", re.compile(r"\*\*Important\b")),
    ("minor", re.compile(r"\*\*Minor\b")),
)
_SEVERITY_JSON = re.compile(
    r"['\"]severity['\"]\s*:\s*['\"](critical|important|minor)['\"]",
    re.IGNORECASE)
_REGENLOOP_BLOCKING = re.compile(r"\*\*blocking\b", re.IGNORECASE)
_REGENLOOP_NONBLOCKING = re.compile(r"\*\*(?:non-blocking|note)\b",
                                    re.IGNORECASE)

_TEST_PATH = re.compile(
    r"(^|/)(tests?|spec)(/|$)|(^|/)[Tt]est_[^/]+$|_test\.(py|go|ts|js)$"
    r"|\.(test|spec)\.(ts|tsx|js|jsx)$")
_DOCS_PATH = re.compile(r"\.(md|rst|txt)$", re.IGNORECASE)

_PROCESS_PHRASES = (
    "pipeline", "ci job", "ci pipeline", "commit split", "mr split",
    "one-concern-per-pr", "one concern per pr", "separable concerns",
    "bundled into one mr", "logically distinct", "insertions across",
    "required approvals", "approvals_required", "no reviewer assigned",
    "reviewer assigned", "approval by the creator",
)
_DECISION_PHRASES = (
    "explicit call", "flagged to the operator", "maintainer decision",
    "operator decision", "judgment call",
)

# Consent reply signals, checked in order (first match wins). The decision
# phrases outrank the decline markers: "SKIPPED — flagged to the operator for
# an explicit call" is a DECISION, not a decline.
_DECISION_CONSENT = (
    "flagged to the operator", "explicit call", "maintainer decision",
    "operator decision",
)
_DEFERRED_CONSENT = (
    "**deferred", "deferred to", "follow-up", "followup", "separate mr",
    "later mr", "backend mr", "next mr", "defer to",
)
_DECLINED_CONSENT = (
    "**skipped**", "**acknowledged", "**answered", "not changing",
    "not rewriting", "won't fix", "wontfix", "by design", "as designed",
    "out of scope", "pre-existing",
)


def is_artifact(body, author=None):
    """True when a note body is a report/brief/summary/trigger artifact —
    never a finding, never nudged, never gated."""
    if not body:
        return True
    stripped = body.strip()
    if stripped in TRIGGER_COMMANDS:
        return True
    for marker in _ARTIFACT_BODY_MARKERS:
        if stripped.startswith(marker) or ("\n" + marker) in body:
            return True
    if _ROUND_SUMMARY_MARKER.search(stripped[:200]) and _ROUND_SUMMARY_HEADER in body:
        return True
    return False


def is_bot_reply(body):
    """Known bot reply templates (nudge boilerplate)."""
    return bool(body) and body.strip().startswith(NUDGE_REPLY_PREFIX)


def extract_severity(text):
    """critical|important|minor from posted-body conventions; unmarked → minor.

    Severity must be EVIDENCED to gate: the fail-open default is minor.
    """
    if not text:
        return "minor"
    head = text[:4000]
    for sev, pattern in _SEVERITY_MARKERS:
        if pattern.search(head):
            return sev
    m = _SEVERITY_JSON.search(head)
    if m:
        return m.group(1).lower()
    if _REGENLOOP_BLOCKING.search(head):
        return "important"
    if _REGENLOOP_NONBLOCKING.search(head):
        return "minor"
    return "minor"


def extract_kind(body, file_path=None):
    """code|test|docs|process|decision — anchor path first, then body."""
    candidates = []
    if file_path:
        candidates.append(file_path)
    if body:
        candidates.extend(re.findall(r"`([\w./-]+\.\w+)`", body[:4000]))
        text = body[:4000].lower()
        if any(p in text for p in _DECISION_PHRASES):
            return "decision"
        if any(p in text for p in _PROCESS_PHRASES):
            return "process"
    if candidates:
        if all(_TEST_PATH.search(c) for c in candidates):
            return "test"
        if all(_DOCS_PATH.search(c) for c in candidates):
            return "docs"
    return "code"


def extract_consent(replies):
    """(disposition, evidence) when a reply records valid consent, else None.

    Consent is a HUMAN disposition recorded in a reply: a deferral to a named
    follow-up, a decline with rationale, or a maintainer decision. Bot
    boilerplate (the nudge template) never counts, even in corpus replay
    where authorship was stripped.
    """
    for reply in replies or []:
        body = reply.get("body", "") if isinstance(reply, dict) else str(reply)
        if not body or is_bot_reply(body):
            continue
        head = body[:2000]
        lowered = head.lower()
        if any(p in lowered for p in _DECISION_CONSENT):
            return "decision", body
        if any(p in lowered for p in _DEFERRED_CONSENT):
            return "deferred", body
        if any(p in lowered for p in _DECLINED_CONSENT):
            return "declined", body
    return None


def classify_thread(thread, verification=None):
    """Classify one fetched thread into a structured finding record.

    `thread` accepts both the live fetch_mr_discussions payload (id, resolved,
    resolvable, file_path, body, replies) and the audit-corpus replay shape
    (discussions[].notes[]). `verification` — when present — is the
    check/sweep result for this thread: {"verdict": "APPLIED" |
    "SILENTLY_APPLIED" | "NOT_APPLIED" | "NEEDS_HUMAN", "reason": str?}.
    """
    body = thread.get("body", "")
    replies = thread.get("replies", []) or []
    record = {
        "id": thread.get("id", ""),
        "file_path": thread.get("file_path"),
        "line_number": thread.get("line_number"),
        "disposition": None,
        "severity": extract_severity(body),
        "kind": extract_kind(body, thread.get("file_path")),
        "reason": "",
    }

    if is_artifact(body):
        record["disposition"] = "artifact"
        return record

    consent = extract_consent(replies)
    verification = verification or {}
    verdict = str(verification.get("verdict", "") or "").upper()

    if consent:
        record["disposition"] = consent[0]
        record["reason"] = "consent recorded in reply"
        return record

    if thread.get("resolved"):
        # Resolved threads are trusted as fixed (current model) — unless a
        # later consumer supplies contrary evidence.
        record["disposition"] = "fixed"
        return record

    if verdict in ("APPLIED", "SILENTLY_APPLIED"):
        record["disposition"] = "fixed"
        record["reason"] = "verification: " + verdict
        return record

    if verdict == "NEEDS_HUMAN":
        # Unclear verification: the thread stays open (not_fixed) and the
        # reason travels with it as a note. Blocking-ness is decided by the
        # gate's severity/kind rule, not by the uncertainty itself.
        record["disposition"] = "not_fixed"
        record["reason"] = verification.get("reason") or "needs human judgment"
        return record

    record["disposition"] = "not_fixed"
    return record


def classify_run(threads, verifications=None):
    """Classify every thread of a run.

    Returns (findings, artifacts): findings are gate-ready records (artifact
    threads excluded), artifacts carry the filtered-out threads so reports
    can show what was excluded and why.
    """
    verifications = verifications or {}
    findings, artifacts = [], []
    for t in threads:
        rec = classify_thread(t, verifications.get(t.get("id", "")))
        if rec["disposition"] == "artifact":
            artifacts.append(rec)
        else:
            findings.append(rec)
    return findings, artifacts


def apply_truncation_guard(findings, truncated_files):
    """Flag unverified findings anchored in diff regions the cap cut.

    Audit fix 5: a thread whose anchor file was truncated away can never be
    verified from the fetched diff — it stays open (fail-closed) and carries
    the stated truncation reason so the report says WHY it is unverified
    instead of silently reading "no relevant change". Findings that already
    carry a verification result are not retro-flagged.
    """
    cut = set(truncated_files or [])
    for f in findings:
        if (f.get("file_path") in cut
                and not f.get("reason", "").startswith("verification:")):
            f["reason"] = ("needs_human: diff truncated before this file's "
                           "region (MAX_DIFF_LINES/MAX_DIFF_CHARS)")
    return findings


def gate(findings):
    """Deterministic gate over classified findings.

    BLOCKED   ⟺ ∃ finding: not_fixed ∧ severity ∈ {critical, important}
               ∧ kind == code (no valid consent — consented threads carry a
               deferred/declined/decision disposition and cannot be not_fixed).
    CLEAN     ⟺ no findings, or every finding fixed.
    Otherwise READY_WITH_NOTES.
    Returns (outcome, blockers, notes).
    """
    blockers, notes = [], []
    for f in findings:
        disp = f.get("disposition")
        if disp in ("fixed", "artifact"):
            continue
        if (disp == "not_fixed"
                and f.get("severity") in BLOCKING_SEVERITIES
                and f.get("kind") == BLOCKING_KIND):
            blockers.append(f)
        else:
            notes.append(f)
    if blockers:
        return "BLOCKED", blockers, notes
    if notes:
        return "READY_WITH_NOTES", [], notes
    return "CLEAN", [], []


def main(argv=None):
    """Thin CLI: classify a JSON run (threads + optional verifications) from
    stdin or --in, print findings + gate outcome as one JSON object."""
    argv = argv or sys.argv[1:]
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--in", dest="inp", default="-",
                        help="input JSON file (default: stdin)")
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.inp == "-" else open(args.inp).read()
    payload = json.loads(raw)
    threads = payload.get("threads", payload if isinstance(payload, list) else [])
    verifications = payload.get("verifications", {}) if isinstance(payload, dict) else {}
    findings, artifacts = classify_run(threads, verifications)
    outcome, blockers, notes = gate(findings)
    print(json.dumps({
        "outcome": outcome,
        "counts": {
            "findings": len(findings),
            "artifacts_filtered": len(artifacts),
            "blockers": len(blockers),
            "notes": len(notes),
        },
        "blockers": blockers,
        "notes": notes,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
