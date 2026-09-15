#!/usr/bin/env python3
"""omni_det_scan.py — det-scan v1 evidence consumer (packet layer + network
engine + CLI).

An external engine scanner stage produces "det-scan v1" evidence packets for
GitLab MRs; this script consumes one packet and posts it onto the MR as
scanner-evidence artifacts: exactly one summary note plus one inline thread
per anchorable finding, every body prefixed `det-scan:`, each thread carrying
an explicit `needs_judgment` disposition. Scanner = evidence, model = verdict
— no plugin component ever auto-resolves these threads, and the flow never
touches GitLab labels.

The module is three layers as they now stand. The packet layer validates and
renders the packet — `load_packet`, `validate_packet` (strict fail-closed
top-level schema; empty findings is a valid clean scan), `packet_mr_matches`
(INTEGER comparison; coercion failures are a clean False), `map_severity`, and
the exact FR-7/FR-11 `thread_body`/`summary_body` templates. The network
engine layer posts through same-directory siblings reused by in-process import
(parsing and assembly are NEVER re-implemented: omni_glab_api transport,
omni_post_review thread_form/path builders/guards, omni_fetch_mr assemble_diff
+ parse_diff_line_map anchor chain), behind the FR-4 head-sha guard, the FR-8
anchor chain, and the FR-3 dedup guard. The CLI layer (parse_args/main/emit)
pins the guard order, posting, dry-run, and the one-JSON-line receipt. Zero
network at import time.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omni_glab_api      # noqa: F401  (T2 transport reuse)
import omni_post_review    # noqa: F401  (T2/T3 form + path reuse)
import omni_fetch_mr       # noqa: F401  (T2 anchor-chain reuse)

EXIT_OK, EXIT_API, EXIT_USAGE, EXIT_GUARD = 0, 1, 2, 3

DET_SCAN_PREFIX = "det-scan:"

TOOLS = ("gitleaks", "opengrep", "trivy")
SCAN_STATUSES = ("ok", "incomplete", "skipped")
TOOL_STATUSES = ("ok", "timeout", "error")
SEVERITIES = ("critical", "high", "medium", "low")
CLASSES = ("secret", "vuln", "sca", "iac", "other")
TOP_LEVEL = {"schema_version", "mr", "scan", "findings", "meta"}


class UsageError(Exception):
    """Bad invocation (missing/invalid arguments or input files) -> exit 2."""


# ── packet loading ─────────────────────────────────────────────────────────


def load_packet(path):
    """Read + JSON-parse a det-scan v1 packet.

    OSError (missing, permissions, is-a-directory) -> UsageError
    "packet-unreadable"; ValueError — covering json.JSONDecodeError AND
    UnicodeDecodeError (invalid UTF-8) — -> UsageError "packet-not-json".
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as e:
        raise UsageError("packet-unreadable: %s" % e)
    except ValueError as e:
        raise UsageError("packet-not-json: %s" % e)


# ── schema validation (first failure wins; unknown nested keys tolerated) ──


def _is_int_like(v):
    """int (non-bool) or a numeric string — mr.project_id / mr.iid shape."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, str) and v.strip().isdigit()


def validate_packet(obj):
    """Return None when obj is a valid det-scan v1 packet, else the first
    failing machine-readable reason string (the CLI's exit-2 vocabulary).

    Strictness applies to TOP-LEVEL keys only (fail-closed on schema
    evolution); nested objects are checked for required keys/enums and
    ignore the rest (v1 additive tolerance). Empty findings is valid.
    """
    if not isinstance(obj, dict):
        return "bad-field:<root>"
    extra = [k for k in obj if k not in TOP_LEVEL]
    if extra:
        return "unknown-top-level-field:%s" % extra[0]

    # schema_version: TYPE-STRICT integer 1 — 1.0 == 1 in Python, but a
    # float 1.0 and a string "1" MUST reject (json round-trips differ).
    v = obj.get("schema_version")
    if isinstance(v, bool) or not isinstance(v, int) or v != 1:
        return "unsupported-schema-version"

    mr = obj.get("mr")
    if not isinstance(mr, dict):
        return "bad-field:mr"
    for k in ("project_id", "iid"):
        if not _is_int_like(mr.get(k)):
            return "bad-field:mr.%s" % k
    for k in ("head_sha", "base_sha"):
        if not isinstance(mr.get(k), str) or not mr.get(k):
            return "bad-field:mr.%s" % k

    scan = obj.get("scan")
    if not isinstance(scan, dict):
        return "bad-field:scan"
    if scan.get("status") not in SCAN_STATUSES:
        return "bad-field:scan.status"
    if not isinstance(scan.get("reason"), str):        # empty is fine
        return "bad-field:scan.reason"
    tools = scan.get("tools")
    if not isinstance(tools, list):
        return "bad-field:scan.tools"
    for i, tool in enumerate(tools):
        if not isinstance(tool, dict):
            return "bad-field:scan.tools[%d]" % i
        if tool.get("name") not in TOOLS:
            return "bad-field:scan.tools[%d].name" % i
        if not isinstance(tool.get("version"), str):
            return "bad-field:scan.tools[%d].version" % i
        duration = tool.get("duration_s")
        if isinstance(duration, bool) \
                or not isinstance(duration, (int, float)):
            return "bad-field:scan.tools[%d].duration_s" % i
        if tool.get("status") not in TOOL_STATUSES:
            return "bad-field:scan.tools[%d].status" % i

    findings = obj.get("findings")
    if not isinstance(findings, list):
        return "bad-field:findings"
    for i, finding in enumerate(findings):
        if not isinstance(finding, dict):
            return "bad-field:findings[%d]" % i
        if finding.get("tool") not in TOOLS:
            return "bad-field:findings[%d].tool" % i
        for k in ("rule_id", "file"):
            if not isinstance(finding.get(k), str) or not finding.get(k):
                return "bad-field:findings[%d].%s" % (i, k)
        line = finding.get("line")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            return "bad-field:findings[%d].line" % i
        if finding.get("severity") not in SEVERITIES:
            return "bad-field:findings[%d].severity" % i
        if finding.get("class") not in CLASSES:
            return "bad-field:findings[%d].class" % i
        if not isinstance(finding.get("preview_redacted"), str):
            return "bad-field:findings[%d].preview_redacted" % i

    meta = obj.get("meta")
    if not isinstance(meta, dict):
        return "bad-field:meta"
    if not isinstance(meta.get("capped"), bool):
        return "bad-field:meta.capped"
    overflow = meta.get("overflow_not_adjudicated")
    if isinstance(overflow, bool) or not isinstance(overflow, int) \
            or overflow < 0:
        return "bad-field:meta.overflow_not_adjudicated"
    redaction = meta.get("redaction")
    if not isinstance(redaction, str) \
            or not redaction.startswith("REDACTED:"):
        return "bad-field:meta.redaction"

    return None


# ── mr cross-check (integer coercion; failures are clean False — A5c) ──────


def _as_int(v):
    """int(v) after str(v).strip(); None on ValueError/TypeError/bool."""
    if isinstance(v, bool):
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def packet_mr_matches(obj, mr_arg, project_arg):
    """Packet is for THIS MR: mr.iid == --mr compared as integers after
    coercion; when --project is numeric it must equal mr.project_id under
    the same coercion (a full-path --project is not comparable — that half
    is skipped). Any coercion failure is a clean False, never a traceback.
    """
    mr = obj.get("mr") if isinstance(obj, dict) else None
    if not isinstance(mr, dict):
        return False
    packet_iid = _as_int(mr.get("iid"))
    arg_iid = _as_int(mr_arg)
    if packet_iid is None or arg_iid is None or packet_iid != arg_iid:
        return False
    if str(project_arg).isdigit():
        packet_project = _as_int(mr.get("project_id"))
        arg_project = _as_int(project_arg)
        if packet_project is None or arg_project is None \
                or packet_project != arg_project:
            return False
    return True


# ── severity mapping + body renderers (EXACT templates — do not drift) ─────


_SEVERITY_MAP = {"critical": "critical", "high": "important",
                 "medium": "minor", "low": "minor"}


def map_severity(s):
    """FR-6: packet severity -> plugin vocabulary. critical stays critical,
    high -> important, medium/low -> minor (downstream extract_severity
    regexes only know Critical/Important/Minor)."""
    return _SEVERITY_MAP[s]


def thread_body(finding):
    """The EXACT FR-7 thread template — single source of truth (downstream
    classification imports this; never re-render it elsewhere). MUST NOT
    contain "Confidence: " or "Found by: " (A4 — those strings would flip
    is_omniforge_note's conjunction and route det-scan threads into the
    never-re-adjudicate priors channel). preview_redacted renders VERBATIM.
    """
    return (
        "det-scan: [%s] %s — %s:%s\n"
        "\n"
        "**%s** — scanner severity %s · class %s\n"
        "\n"
        "%s\n"
        "\n"
        "**Disposition: needs_judgment** — scanner evidence, not a verdict. "
        "This thread is\n"
        "adjudicated by the review agents and is never auto-resolved."
        % (finding["tool"], finding["rule_id"], finding["file"],
           finding["line"], map_severity(finding["severity"]).capitalize(),
           finding["severity"], finding["class"],
           finding["preview_redacted"]))


def summary_body(packet, iid, anchored_count, unanchored):
    """The EXACT FR-11 summary template: scan status + reason, one line per
    tool, findings counts, capped/overflow, redaction, and the
    needs_judgment explainer. The Unanchored section (heading + one line
    per finding) renders ONLY when U > 0 — unanchorable findings are never
    silently dropped. NO severity marker tokens anywhere (FR-6: the summary
    must not pollute extract_severity).
    """
    scan = packet["scan"]
    meta = packet["meta"]
    findings = packet["findings"]
    lines = [
        "det-scan: scanner evidence — MR !%s @ %s" % (
            iid, packet["mr"]["head_sha"][:8]),
        "",
        "**Scan status:** %s — %s" % (scan["status"], scan["reason"]),
        "",
        "**Tools:**",
    ]
    for tool in scan["tools"]:
        lines.append("- %s %s — %s (%ss)" % (
            tool["name"], tool["version"], tool["status"],
            tool["duration_s"]))
    lines += [
        "",
        "**Findings:** %d total · %d anchored (threads below) · "
        "%d unanchored (see below)"
        % (len(findings), anchored_count, len(unanchored)),
        "**Capped:** %s · overflow not adjudicated: %s"
        % (meta["capped"], meta["overflow_not_adjudicated"]),
        "**Redaction:** %s" % meta["redaction"],
        "",
        "**How to read this:** the scanner is evidence, the model is the "
        "verdict. Every `det-scan:`",
        "thread below carries disposition `needs_judgment` and is "
        "adjudicated by the review agents —",
        "this note is not a verdict and these threads are never "
        "auto-resolved.",
    ]
    if unanchored:
        lines += [
            "",
            "### Unanchored evidence (no anchorable diff line — "
            "adjudicate from the file)",
        ]
        for finding in unanchored:
            lines.append("- [%s] %s — %s:%s — severity %s — %s" % (
                finding["tool"], finding["rule_id"], finding["file"],
                finding["line"], finding["severity"],
                finding["preview_redacted"]))
    return "\n".join(lines)


# ── network engine (T2): head-sha guard, anchor chain, dedup guard ─────────


def head_sha_matches(packet_head, refs_head):
    """FR-4: packet.mr.head_sha vs the MR's diff_refs.head_sha (the SAME sha
    later used in position[head_sha]), CASE-INSENSITIVE (GitLab hex shas may
    arrive upper- or lower-case). Empty on either side never matches —
    never anchor evidence onto an unknown head."""
    left = (packet_head or "").lower()
    right = (refs_head or "").lower()
    return bool(left) and bool(right) and left == right


def fetch_anchor_map(project, mr, token, host, attempts, backoff_base):
    """FR-8 anchor map — the three-step reuse chain (parsing and assembly
    are NEVER re-implemented; NEVER a since-sha/delta diff — the full-MR
    diff is the anchoring surface):

    1. paginated GET <mr_path>/diffs via omni_glab_api.get_all — the REAL
       array-of-items JSON shape [{diff, old_path, new_path, ...}];
    2. omni_fetch_mr.assemble_diff(items) — reconstructs the `+++ b/`-headed
       diff text (the raw /diffs response carries headerless per-file hunks;
       feeding it straight to the parser yields an EMPTY map — the sibling
       exists precisely for this);
    3. omni_fetch_mr.parse_diff_line_map(text) — the same pure function that
       builds gather.json's diff_line_map.
    """
    items = omni_glab_api.get_all(
        omni_post_review.mr_path(project, mr) + "/diffs", token, host=host,
        attempts=attempts, backoff_base=backoff_base)
    text = omni_fetch_mr.assemble_diff(items)
    return omni_fetch_mr.parse_diff_line_map(text)


def anchorable(finding, anchor_map):
    """A finding is anchorable iff its file is in the map AND its line is in
    that file's added_lines — position[new_line] must land on a line the MR
    actually added."""
    return finding["file"] in anchor_map \
        and finding["line"] in anchor_map[finding["file"]]["added_lines"]


def split_findings(packet, anchor_map):
    """Split packet findings into (anchored, unanchored) — packet findings
    order preserved in BOTH lists. Unanchorable findings (wrong file, wrong
    line, or both) are never silently dropped: they are returned for
    disclosure in the summary (FR-8)."""
    anchored, unanchored = [], []
    for finding in packet["findings"]:
        if anchorable(finding, anchor_map):
            anchored.append(finding)
        else:
            unanchored.append(finding)
    return anchored, unanchored


def newer_det_scan_note(project, mr, since, token, host, attempts,
                        backoff_base):
    """Return the det-scan summary note newer than `since`, else None —
    mirroring omni_post_review.newer_summary_note EXACTLY: the listing
    paginates via omni_glab_api.get_all (per_page=100 — a busy MR with 100+
    notes cannot hide a prior det-scan note on page 2), the body matches
    with lstrip().startswith(DET_SCAN_PREFIX) (leading whitespace tolerated;
    a mid-body occurrence never trips the guard), created_at goes through
    the sibling iso_to_epoch (an unparseable timestamp never trips the
    guard), and the note is returned iff created is not None and
    created > since (FR-3, SC-2b)."""
    notes = omni_glab_api.get_all(
        omni_post_review.notes_base_path(project, mr), token, host=host,
        attempts=attempts, backoff_base=backoff_base)
    for note in notes:
        if not isinstance(note, dict):
            continue
        body = note.get("body") or ""
        if not body.lstrip().startswith(DET_SCAN_PREFIX):
            continue
        created = omni_post_review.iso_to_epoch(note.get("created_at"))
        if created is not None and created > since:
            return note
    return None


# ── CLI (T3): guard order, posting, dry-run, receipt (FR-2/5/7-11, A5) ──────


def parse_args(argv=None):
    """The det-scan consumer flags. NO --token — the token resolves from
    env only (GITLAB_TOKEN > OMNIFORGE_GITLAB_TOKEN), and only on real
    (non-dry) runs after every local guard has passed (A5d)."""
    ap = argparse.ArgumentParser(
        description="det-scan v1 evidence consumer: one packet in, one "
                    "summary note + one inline thread per anchorable "
                    "finding out (one JSON receipt line on stdout).")
    ap.add_argument("--packet", required=True,
                    help="path to the det-scan v1 evidence packet JSON")
    ap.add_argument("--project", required=True,
                    help="GitLab project ID, pre-encoded URL path, or bare "
                         "full path (group/subgroup/project — URL-encoded "
                         "automatically)")
    ap.add_argument("--mr", required=True, help="merge request IID")
    ap.add_argument("--since", type=float, default=0,
                    help="scan-round epoch for the staleness and dedup "
                         "guards (default 0: the age guard is dormant and "
                         "any existing det-scan note refuses)")
    ap.add_argument("--host", default=None,
                    help="GitLab host (default: GITLAB_HOST env, "
                         "CI_API_V4_URL env, then https://gitlab.com)")
    ap.add_argument("--force", action="store_true",
                    help="override the det-scan dedup guard")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the planned API calls, execute nothing "
                         "(works without a token)")
    ap.add_argument("--packet-epoch", type=float, default=None,
                    help="override the packet file's mtime as its "
                         "effective epoch")
    ap.add_argument("--attempts", type=int, default=3,
                    help="max attempts per API call (default 3)")
    ap.add_argument("--backoff-base", type=float, default=2,
                    help="exponential backoff base seconds "
                         "(default 2: 2 s then 4 s)")
    args = ap.parse_args(argv)
    if args.attempts < 1:
        ap.error("--attempts must be >= 1")            # exits 2
    if args.backoff_base < 0:
        ap.error("--backoff-base must be >= 0")
    return args


def main(argv=None):
    # 1. parse + start
    start = time.time()
    args = parse_args(argv)

    # 2. load / validate / mr-match -> exit 2, NO stdout receipt
    try:
        pkt = load_packet(args.packet)
    except UsageError as e:
        print("omni_det_scan: %s" % e, file=sys.stderr)
        return EXIT_USAGE
    reason = validate_packet(pkt)
    if reason is None and not packet_mr_matches(pkt, args.mr, args.project):
        reason = "packet-mr-mismatch"
    if reason is not None:
        print("omni_det_scan: %s" % reason, file=sys.stderr)
        return EXIT_USAGE

    findings = pkt["findings"]

    # 3. staleness (local, no network): --packet-epoch overrides mtime
    effective_epoch = args.packet_epoch if args.packet_epoch is not None \
        else os.stat(args.packet).st_mtime

    # Live posting state — the exit-1 receipt reports exactly the artifacts
    # that succeeded (prior artifacts stay; no rollback).
    counts = {"posted_summary": False, "threads": 0, "unanchored": 0,
              "failures": 0}

    def emit(action="skipped", skip_reason=None, posted_summary=None,
             threads=None, unanchored=None, planned_findings=0, dry_run=False,
             failures=None):
        """Print exactly ONE JSON receipt line — exits 0/1/3 only, never 2.
        Unspecified counts fall through to the live posting state; the
        receipt carries counts and shas ONLY (never previews or packet body
        text); planned_findings is nonzero only on the dry-run path."""
        receipt = {
            "action": action,
            "skip_reason": skip_reason,
            "posted_summary": counts["posted_summary"]
            if posted_summary is None else posted_summary,
            "threads": counts["threads"] if threads is None else threads,
            "unanchored": counts["unanchored"]
            if unanchored is None else unanchored,
            "planned_findings": planned_findings,
            "findings_total": len(findings),
            "scan_status": pkt["scan"]["status"],
            "packet_epoch": effective_epoch,
            "head_sha": pkt["mr"]["head_sha"],
            "dry_run": dry_run,
            "failures": counts["failures"] if failures is None else failures,
            "elapsed_ms": int(round((time.time() - start) * 1000)),
        }
        print(json.dumps(receipt))
        return receipt

    if effective_epoch < args.since:
        print("omni_det_scan: REFUSING to post — stale packet: epoch %s < "
              "--since %s" % (effective_epoch, args.since), file=sys.stderr)
        emit(skip_reason="stale-packet")
        return EXIT_GUARD

    # 4. scan-status skipped: the benign exit-0 no-op — NOTHING posted, no
    # network (evaluated BEFORE every network guard, R10: a skipped scan
    # reports the skip regardless of head state)
    if pkt["scan"]["status"] == "skipped":
        emit(action="skipped",
             skip_reason="scan-skipped:%s" % pkt["scan"]["reason"])
        return EXIT_OK

    # 5. dry-run: all local guards done, the token is NEVER resolved here,
    # zero transport calls. N+1 plan lines — ONE notes POST for the summary
    # plus ONE discussions POST per packet finding (ALL N: anchorability is
    # unknown without the diff) rendered with the sibling PLACEHOLDER_REFS.
    if args.dry_run:
        omni_post_review.dry_line(
            "POST", omni_post_review.notes_base_path(args.project, args.mr),
            [("body", summary_body(pkt, args.mr, len(findings), []))])
        for finding in findings:
            omni_post_review.dry_line(
                "POST",
                omni_post_review.discussions_path(args.project, args.mr),
                omni_post_review.thread_form(
                    thread_body(finding), omni_post_review.PLACEHOLDER_REFS,
                    finding["file"], finding["line"]))
        emit(action="posted", posted_summary=True, threads=0, unanchored=0,
             planned_findings=len(findings), dry_run=True)
        return EXIT_OK

    # 6. token (A5d: AFTER all local guards — a stale packet with no token
    # already exited 3 above), env only
    token = omni_glab_api.resolve_token()
    if not token:
        print("omni_det_scan: GITLAB_TOKEN is not set — fix:\n  %s"
              % omni_post_review.TOKEN_FIX, file=sys.stderr)
        return EXIT_USAGE

    try:
        # 7. head-sha (network guard 1): the ONE MR metadata GET per
        # invocation (N+1 discipline) — its refs also anchor every thread
        refs, _meta = omni_post_review.fetch_mr(
            args.project, args.mr, token, args.host, args.attempts,
            args.backoff_base, need_refs=True)
        if not head_sha_matches(pkt["mr"]["head_sha"],
                                refs.get("head_sha", "")):
            print("omni_det_scan: REFUSING to post — packet head %s != MR "
                  "head %s" % (pkt["mr"]["head_sha"][:8],
                               refs.get("head_sha", "")[:8]),
                  file=sys.stderr)
            emit(skip_reason="head-sha-mismatch")
            return EXIT_GUARD

        # 8. dedup (network guard 2): runs AFTER head-sha so a moved head
        # reports head-sha-mismatch, not a misleading dedup refusal
        if not args.force:
            note = newer_det_scan_note(args.project, args.mr, args.since,
                                       token, args.host, args.attempts,
                                       args.backoff_base)
            if note is not None:
                print("omni_det_scan: REFUSING to post — a det-scan note "
                      "(created %s) newer than --since %s already exists on "
                      "MR !%s; pass --force to override"
                      % (note.get("created_at"), args.since, args.mr),
                      file=sys.stderr)
                emit(skip_reason="det-scan-already-posted")
                return EXIT_GUARD

        # 9. anchor + post: summary FIRST, then one thread per ANCHORABLE
        # finding in packet order — unanchorable findings are disclosed in
        # the summary, never silently dropped (FR-8)
        anchor_map = fetch_anchor_map(args.project, args.mr, token, args.host,
                                      args.attempts, args.backoff_base)
        anchored, unanchored = split_findings(pkt, anchor_map)
        counts["unanchored"] = len(unanchored)
        omni_glab_api.request(
            "POST", omni_post_review.notes_base_path(args.project, args.mr),
            token, host=args.host,
            form=[("body", summary_body(pkt, args.mr, len(anchored),
                                        unanchored))],
            attempts=args.attempts, backoff_base=args.backoff_base)
        counts["posted_summary"] = True
        for finding in anchored:
            omni_glab_api.request(
                "POST",
                omni_post_review.discussions_path(args.project, args.mr),
                token, host=args.host,
                form=omni_post_review.thread_form(
                    thread_body(finding), refs, finding["file"],
                    finding["line"]),
                attempts=args.attempts, backoff_base=args.backoff_base)
            counts["threads"] += 1
    except (omni_glab_api.GlabApiError, omni_post_review.PostingError) as e:
        counts["failures"] += 1
        print("omni_det_scan: %s" % e, file=sys.stderr)
        emit(action="posted")
        return EXIT_API

    # 10. posted
    emit(action="posted", posted_summary=True, threads=len(anchored))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
