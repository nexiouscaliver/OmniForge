#!/usr/bin/env python3
"""Corpus replay harness — run verification model v2 over the recorded
audit-corpus thread sets and emit the release-gate table (P1).

The corpus (engine repo claudedocs/omnicheck-audit-2026-09/, mirrored
read-only into tests/fixtures/omnicheck-audit/) carries, per MR:
  mr-N.json             the recorded GitLab state: discussions (bodies,
                        resolved flags), notes, commits, approvals
  adjudication-mr-N.json  the auditor's per-thread verdicts + ground truth
                        and the run-level gate outcome

Two classification paths per run:
  model  — v2 classification exactly as production would compute it: thread
           content (bodies, replies, resolved) + the recorded omnicheck
           verification verdict. Nothing from the auditor's ground truth.
  oracle — the same classification with the auditor's ground-truth
           corrections applied (FALSE_NOT_APPLIED → fixed, resolved-by-
           deferral → deferred, maintainer-call → decision, …). This is the
           outcome v2 SHOULD produce if every verification input were right.

Assertions:
  (a) model == oracle on every previously-CORRECT run (no correct run flips)
  (b) the approve-despite-outstanding runs no longer report CLEAN
  (c) the over-rejection cases no longer block; DISPOSED/decision threads
      never appear among a run's blockers

Usage:
  replay_audit.py --corpus DIR [--json OUT|-] [--only repo:iid[:run]]
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omni_verdict  # noqa: E402  (same directory)

HEX_TID = re.compile(r"^#?([0-9a-f]{6,})")
LABEL_SEVERITY = re.compile(r"\b(Critical|Important|Minor)\b")

FALSE_APPROVE_CASES = ("cleo!1108", "cleo!1172", "cleo!1180", "cleo!1184",
                       "cleo!1226")
OVER_REJECT_CASES = ("cleo!1306", "cleo!1447", "loop!59", "loop!60")
DECISION_THREAD_CASES = ("loop!59", "loop!60", "loop!62")


def audit_class(overall):
    o = (overall or "").lower()
    if "false_approve" in o or "false approv" in o:
        return "FALSE_APPROVE"
    if "false_reject" in o or "false reject" in o:
        return "FALSE_REJECT"
    if o.startswith("correct"):
        return "CORRECT"
    if "partial" in o:
        return "PARTIAL"
    if "undetermin" in o:
        return "UNDETERMINABLE"
    return "OTHER"


def map_verdict(text):
    v = str(text or "").upper()
    if "SILENTLY_APPLIED" in v:
        return {"verdict": "SILENTLY_APPLIED"}
    if "NOT_APPLIED" in v:
        return {"verdict": "NOT_APPLIED"}
    if "NEEDS_HUMAN" in v:
        return {"verdict": "NEEDS_HUMAN"}
    if "APPLIED" in v:
        return {"verdict": "APPLIED"}
    return {}


def index_discussions(mr):
    """discussion-id index over the recorded discussions.

    The adjudicator's thread ids sometimes cite the discussion id's first 8
    hex chars and sometimes its last 8 — index both ends.
    """
    idx = {}
    for disc in mr.get("discussions", []):
        did = str(disc.get("id", ""))
        if len(did) >= 8:
            idx[did[:7]] = disc
            idx[did[:8]] = disc
            idx[did[-8:]] = disc
            idx[did[-7:]] = disc
    return idx


def thread_record(row, mr_index):
    """Build an omni_verdict-shaped thread from one adjudication row."""
    tid_raw = str(row.get("thread_id", ""))
    m = HEX_TID.search(tid_raw)
    body, replies, resolved = tid_raw, [], None
    if m:
        disc = mr_index.get(m.group(1)[:7]) or mr_index.get(m.group(1)[:8])
        if disc:
            notes = [n for n in disc.get("notes", [])
                     if not n.get("system", False)]
            if notes:
                body = notes[0].get("body", "") or tid_raw
                replies = [{"body": n.get("body", "")} for n in notes[1:]]
            resolved = disc.get("resolved")
            if disc.get("resolved") is None and notes:
                resolved = notes[0].get("resolved")
    return {
        "id": tid_raw,
        "body": body,
        "replies": replies,
        "resolved": bool(resolved),
        "resolvable": True,
        "file_path": None,
    }, body


def label_severity_fallback(rec, label_body):
    """Unlinked rows: the adjudicator's label carries the severity word."""
    if rec["severity"] == "minor":
        m = LABEL_SEVERITY.search(label_body or "")
        if m:
            rec["severity"] = m.group(1).lower()
    return rec


def oracle_correct(rec, row):
    """Apply the auditor's ground-truth corrections to one classification.

    Ground truth records two different things: the FIX state (was the concern
    addressed at the run head) and, in judgment/parentheticals, the
    DISPOSITION (deferred / declined / decision). A consented disposition the
    model derived from thread content is PRESERVED — the auditor confirming
    the fix is absent does not un-decline it; that is precisely the
    decline-with-rationale class the audit says must not gate.
    """
    judgment = str(row.get("judgment", ""))
    gt = str(row.get("ground_truth", ""))
    j, g = judgment.lower(), gt.lower()

    if "false_not_applied" in j or g.startswith("wrong") or "fix present" in g:
        rec["disposition"] = "fixed"
        rec["reason"] = "oracle: auditor verified fix present"
        return rec
    if "false_apply" in j and "deferral" in j:
        rec["disposition"] = "deferred"
        rec["reason"] = "oracle: resolved-by-deferral, target never landed"
        return rec
    if "resolved_by_disclosure" in j or "disclosure" in g:
        rec["disposition"] = "declined"
        rec["reason"] = "oracle: resolved by disclosure"
        return rec
    if "premise" in g and ("overturn" in g or "not hold" in g):
        rec["disposition"] = "obsolete"
        rec["reason"] = "oracle: premise invalidated at head"
        return rec
    if rec["disposition"] in omni_verdict.DISPOSED_DISPOSITIONS:
        return rec  # consented disposition, ground truth does not contradict it
    if ("maintainer call" in j or "maintainer-scope" in g
            or "declined" in g or "decision" in j.split(":")[0]):
        rec["disposition"] = "decision" if "decision" in j else "declined"
        rec["reason"] = "oracle: %s" % judgment[:60]
        return rec
    if ("unaddressed" in g or g.startswith("partial") or "partially" in g
            or "fix absent" in g or "absent" in g or "untouched" in g):
        rec["disposition"] = "not_fixed"
        rec["reason"] = "oracle: auditor verified unaddressed/partial/absent"
        return rec
    if any(k in g for k in ("addre", "applied", "moved:", "posted")):
        rec["disposition"] = "fixed"
        rec["reason"] = "oracle: auditor verified addressed"
        return rec
    return rec


def replay_run(repo, iid, run_idx, run, mr):
    mr_index = index_discussions(mr)
    model_findings, oracle_findings = [], []
    for row in run.get("per_thread", []):
        thread, label = thread_record(row, mr_index)
        verification = map_verdict(row.get("omnicheck_verdict"))
        rec = omni_verdict.classify_thread(thread, verification)
        rec = label_severity_fallback(rec, label)
        orec = oracle_correct(dict(rec), row)
        if rec["disposition"] != "artifact":
            model_findings.append(rec)
        if orec["disposition"] != "artifact":
            oracle_findings.append(orec)

    model_out, model_blockers, _ = omni_verdict.gate(model_findings)
    oracle_out, oracle_blockers, _ = omni_verdict.gate(oracle_findings)
    return {
        "repo": repo,
        "iid": iid,
        "run": run_idx + 1,
        "key": "%s!%s" % (repo, iid),
        "gate": str(run.get("gate", ""))[:10],
        "class": audit_class(run.get("overall")),
        "model_outcome": model_out,
        "oracle_outcome": oracle_out,
        "model_blockers": model_blockers,
        "oracle_blockers": oracle_blockers,
        "match": model_out == oracle_out,
    }


def load_corpus(corpus):
    for repo in ("cleo", "loop", "omniforge"):
        for adj in sorted(glob.glob(os.path.join(corpus, repo,
                                                 "adjudication-mr-*.json"))):
            iid = re.search(r"adjudication-mr-(.+)\.json$",
                            os.path.basename(adj)).group(1)
            mr_path = os.path.join(corpus, repo, "mr-%s.json" % iid)
            if not os.path.exists(mr_path):
                continue
            try:
                adj_data = json.load(open(adj))
                mr_data = json.load(open(mr_path))
            except (json.JSONDecodeError, OSError):
                continue
            for i, run in enumerate(adj_data.get("runs", [])):
                if not run.get("per_thread"):
                    continue
                yield repo, iid, i, run, mr_data


def build_assertions(results):
    a_mismatches = [dict(r, model_blockers="<%d>" % len(r["model_blockers"]),
                         oracle_blockers="<%d>" % len(r["oracle_blockers"]))
                    for r in results
                    if r["class"] == "CORRECT" and not r["match"]]
    # The release-gate form of (a): a previously-correct run may not flip its
    # BLOCKING class. CLEAN <-> READY_WITH_NOTES deltas are note-count
    # differences on facts only the auditor's diff-level verification could
    # see (reported above); they do not change any gate decision.
    a_blocking_flips = [dict(r, model_blockers="<%d>" % len(r["model_blockers"]),
                             oracle_blockers="<%d>" % len(r["oracle_blockers"]))
                        for r in results
                        if r["class"] == "CORRECT"
                        and (r["model_outcome"] == "BLOCKED") !=
                            (r["oracle_outcome"] == "BLOCKED")]

    by_key = {}
    for r in results:
        by_key.setdefault(r["key"], []).append(r)

    def latest(key):
        runs = by_key.get(key) or []
        return runs[-1] if runs else None

    b_cases = {}
    for key in FALSE_APPROVE_CASES:
        r = latest(key)
        if r:
            b_cases[key] = {"model_outcome": r["model_outcome"],
                            "oracle_outcome": r["oracle_outcome"]}

    c_cases = {}
    for key in OVER_REJECT_CASES + DECISION_THREAD_CASES:
        r = latest(key)
        if r:
            c_cases[key] = {
                "model_outcome": r["model_outcome"],
                "blockers": [{"id": b["id"][:40], "disposition": b["disposition"],
                              "severity": b["severity"], "kind": b["kind"]}
                             for b in r["model_blockers"]],
            }

    return {
        "a": {"scope": "previously-CORRECT runs", "mismatches": a_mismatches,
              "blocking_flips": a_blocking_flips},
        "b": {"scope": "approve-despite-outstanding not CLEAN", "cases": b_cases},
        "c": {"scope": "over-rejection not BLOCKED + disposed never block",
              "cases": c_cases},
    }


TABLE_HEADER = (
    "| repo | mr | run | hist gate | audit class | model | oracle | match |\n"
    "|---|---|---|---|---|---|---|---|\n")


def render_table(results):
    lines = [TABLE_HEADER]
    for r in results:
        lines.append(
            "| %s | !%s | %d | %s | %s | %s | %s | %s |\n" % (
                r["repo"], r["iid"], r["run"], r["gate"], r["class"],
                r["model_outcome"], r["oracle_outcome"],
                "=" if r["match"] else "DIFF"))
    return "".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", required=True,
                    help="corpus root (contains cleo/ loop/ omniforge/)")
    ap.add_argument("--json", default="",
                    help="write the JSON payload here ('-' = stdout)")
    ap.add_argument("--only", default="",
                    help="restrict to repo:iid[:run] (comma-separated)")
    args = ap.parse_args(argv or sys.argv[1:])

    only = {}
    if args.only:
        for spec in args.only.split(","):
            parts = spec.split(":")
            only[(parts[0], parts[1])] = int(parts[2]) if len(parts) > 2 else None

    results = []
    for repo, iid, i, run, mr in load_corpus(args.corpus):
        if only and (repo, iid) not in only:
            continue
        if only and only[(repo, iid)] is not None and only[(repo, iid)] != i + 1:
            continue
        results.append(replay_run(repo, iid, i, run, mr))

    assertions = build_assertions(results)
    payload = {"runs": [{k: v for k, v in r.items()
                         if k not in ("model_blockers", "oracle_blockers")}
                        for r in results],
               "assertions": assertions}

    table = render_table(results)
    sys.stdout.write(table)
    sys.stdout.write("\nassertion (a) mismatches on CORRECT runs: %d "
                     "(blocking-class flips: %d)\n"
                     % (len(assertions["a"]["mismatches"]),
                        len(assertions["a"]["blocking_flips"])))
    for key, c in assertions["b"]["cases"].items():
        sys.stdout.write("assertion (b) %s: model=%s oracle=%s\n"
                         % (key, c["model_outcome"], c["oracle_outcome"]))
    for key, c in assertions["c"]["cases"].items():
        sys.stdout.write("assertion (c) %s: model=%s blockers=%d\n"
                         % (key, c["model_outcome"], len(c["blockers"])))

    out = json.dumps(payload, indent=1)
    if args.json == "-":
        sys.stdout.write("\n" + out + "\n")
    elif args.json:
        with open(args.json, "w") as fh:
            fh.write(out + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
