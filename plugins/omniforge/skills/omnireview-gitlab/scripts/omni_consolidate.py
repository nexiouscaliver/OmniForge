#!/usr/bin/env python3
"""omni_consolidate.py — deterministic consolidation of validated findings files.

Merges ONLY near-identical findings (same normalized file AND overlapping
line_range intervals AND same normalized category AND token-set Jaccard
similarity of one_liner+evidence >= --similarity). Everything else is
CLUSTERED, never merged: per-agent entries ride along VERBATIM (byte-identical
fields as validated). NO confidence arithmetic anywhere: every confidence in
the output is byte-identical to an agent-assigned input value — never
adjusted, never recomputed. Corroboration is count/of metadata only.

Outputs to --out-dir: clusters.json (cluster list, creation order) and
worklist.md — ONE adjudication worklist for a single main-agent pass.
Stdout: exactly one JSON line. Diagnostics: stderr. Stdlib only.

CLI:
  python3 omni_consolidate.py --findings <f1.json> [<f2.json> [<f3.json>]] \
      [--prior <prior-findings.json>] --out-dir <dir> \
      [--threshold 70] [--similarity 0.30]

Exit codes: 0 success (including empty findings and degraded --prior input);
2 usage errors (bad flags, more than 3 findings files, unreadable/invalid
findings file).
"""

import argparse
import json
import os
import re
import sys
from itertools import combinations

STOPWORDS = frozenset("""a an the and or but if to of in on at by for with from into as is are
was were be been it its this that these those not no can could should would will do does did
has have had""".split())

SEV_RANK = {"minor": 0, "important": 1, "critical": 2}

CONFIDENCE_FLOOR = 50        # below this a finding never surfaces anywhere
DEFAULT_THRESHOLD = 70       # posting threshold on AGENT-ASSIGNED scores only
DEFAULT_SIMILARITY = 0.30    # calibrated on the W6 fleet (T6): near-dup pairs
                            # score 0.12-0.47, distinct-substance ceiling 0.24

REASON_ALREADY = "already_adjudicated"
REASON_CONFLICT = "conflict"
REASON_CROSS = "cross_category_same_locus"
REASON_SAME_LOCUS = "same_locus_distinct_findings"
REASON_SUB = "sub_threshold"

WORKLIST_TITLE = ("# Consolidation worklist — consume in ONE pass, top to bottom, "
                  "in a single response")

SEC_ALREADY = "## Already adjudicated (carry forward as-is — never re-adjudicated)"
SEC_CONFLICT = "## Needs Human Judgment (conflict — both perspectives verbatim)"
SEC_CROSS = "## Cross-category, same locus (keep both)"
SEC_SAME_LOCUS = ("## Same locus, distinct findings "
                  "(not near-identical — keep each perspective)")
SEC_SUB = "## Sub-threshold observations (confidence 50–69)"
SEC_AUTO = "## Auto clusters (no adjudication needed — straight into the Phase 5 report)"

# Worklist consumption order (single pass, top to bottom): the first matching
# reason decides which section a cluster lands in.
SECTION_FOR_REASON = [
    (REASON_ALREADY, SEC_ALREADY),
    (REASON_CONFLICT, SEC_CONFLICT),
    (REASON_CROSS, SEC_CROSS),
    (REASON_SAME_LOCUS, SEC_SAME_LOCUS),
    (REASON_SUB, SEC_SUB),
]


def norm_path(p):
    """Normalized comparison path: lowercase, no leading ./, POSIX separators.

    Non-string values (T1 does not validate `file` type) are treated as null —
    never merge, never crash.
    """
    if not isinstance(p, str):
        return None
    p = p.strip().replace("\\", "/")
    while p.startswith("./"):        # strip ONLY ./ prefixes — a leading dot as in
        p = p[2:]                    # .hidden.py is part of the filename
    return p.lower() or None


def norm_cat(c):
    """Normalized comparison category; non-strings are the same unknown (None)."""
    if not isinstance(c, str):
        return None
    c = c.strip().lower()
    return c or None


def toks(f):
    """Token set of one_liner + evidence for similarity comparison."""
    parts = []
    for k in ("one_liner", "evidence"):
        v = f.get(k)
        parts.append(v if isinstance(v, str) else "" if v is None else str(v))
    return set(re.findall(r"[a-z0-9]+", " ".join(parts).lower())) - STOPWORDS


def jaccard(f1, f2):
    a, b = toks(f1), toks(f2)
    return len(a & b) / len(a | b) if (a | b) else 0.0


def overlaps(lo1, hi1, lo2, hi2):
    """Inclusive interval overlap; null bounds never overlap."""
    if lo1 is None or lo2 is None:
        return False
    return lo1 <= hi2 and lo2 <= hi1


def near_identical(f1, f2, sim):
    """Merge predicate: same file + overlapping lines + same category + sim."""
    return (f1["_file"] is not None and f1["_file"] == f2["_file"]
            and overlaps(f1["_lo"], f1["_hi"], f2["_lo"], f2["_hi"])
            and norm_cat(f1.get("category")) == norm_cat(f2.get("category"))
            and jaccard(f1, f2) >= sim)


def valid_range(lr):
    return (isinstance(lr, list) and len(lr) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in lr))


def load_findings(paths, anomalies):
    """Load validated findings files in argument order (deterministic order).

    Working copies carry _-prefixed helper keys (_file/_lo/_hi); the output
    entries keep every original field verbatim. Confidence < CONFIDENCE_FLOOR
    never surfaces anywhere (policy drop, silent). Non-string file/category
    are tolerated as null/unknown with an anomaly note — never a crash.
    """
    findings = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            print("omni_consolidate: cannot read findings file %s: %s" % (path, e),
                  file=sys.stderr)
            raise SystemExit(2)
        if not isinstance(data, dict):
            anomalies.append("%s: top-level JSON is not an object — treated as empty"
                             % path)
            items = []
        else:
            items = data.get("findings", [])
        if not isinstance(items, list):
            anomalies.append("%s: findings is not a list — treated as empty" % path)
            items = []
        for i, f in enumerate(items, 1):
            if not isinstance(f, dict):
                anomalies.append("%s finding %d: not an object — skipped" % (path, i))
                continue
            conf = f.get("confidence")
            if not isinstance(conf, int) or isinstance(conf, bool):
                anomalies.append("%s finding %d: confidence %r not an integer — dropped"
                                 % (path, i, conf))
                continue
            if conf < CONFIDENCE_FLOOR:
                continue
            g = dict(f)
            raw_file = f.get("file")
            if raw_file is not None and not isinstance(raw_file, str):
                anomalies.append("%s finding %d: file %r is not a string — treated as "
                                 "null (never merges)" % (path, i, raw_file))
                g["_file"] = None
            else:
                g["_file"] = norm_path(raw_file)
            raw_cat = f.get("category")
            if raw_cat is not None and not isinstance(raw_cat, str):
                anomalies.append("%s finding %d: category %r is not a string — treated "
                                 "as unknown" % (path, i, raw_cat))
            lr = f.get("line_range")
            if lr is None:
                g["_lo"] = g["_hi"] = None
            elif valid_range(lr):
                g["_lo"], g["_hi"] = min(lr), max(lr)
            else:
                anomalies.append("%s finding %d: line_range %r invalid — treated as null"
                                 % (path, i, lr))
                g["_lo"] = g["_hi"] = None
            findings.append(g)
    return findings


def build_groups(findings):
    """Group findings by locus: same normalized file + overlapping line_range
    (chained overlap via the group's expanding [min_start, max_end] interval).
    Null-file / null-range findings never join — always singletons."""
    groups = []
    for g in findings:
        target = None
        if g["_file"] is not None and g["_lo"] is not None:
            for grp in groups:
                if (grp["file"] == g["_file"] and grp["lo"] is not None
                        and grp["lo"] <= g["_hi"] and g["_lo"] <= grp["hi"]):
                    target = grp
                    break
        if target is None:
            groups.append({"file": g["_file"], "lo": g["_lo"], "hi": g["_hi"],
                           "entries": [g]})
        else:
            target["entries"].append(g)
            target["lo"] = min(target["lo"], g["_lo"])
            target["hi"] = max(target["hi"], g["_hi"])
    return groups


def pick_rep(entries):
    """Representative = highest confidence; ties: lexicographic agent, then
    entry position. Used for ordering/anchoring only — never for scores."""
    best = max(e["confidence"] for e in entries)
    candidates = [(i, e) for i, e in enumerate(entries) if e["confidence"] == best]
    return min(candidates, key=lambda t: (str(t[1].get("agent", "")), t[0]))[1]


def strip_internal(e):
    return {k: v for k, v in e.items() if not k.startswith("_")}


def load_priors(path, anomalies):
    """Load prior findings (retrospective guard input).

    Producer shape: {thread_id, resolved (bool), file_path, line_number, body}.
    Aliases tolerated: {file, line_range, state: "resolved"|"open"}. Matching is
    deliberately WEAKER than the merge predicate: same file + line overlap.
    An unreadable/invalid prior file degrades to no priors — never blocks.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        anomalies.append("prior file %s unreadable (%s) — continuing without priors"
                         % (path, e))
        return []
    entries = data.get("prior_findings") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        anomalies.append("prior file %s carries no prior_findings list — continuing "
                         "without priors" % path)
        return []
    priors = []
    for i, p in enumerate(entries, 1):
        if not isinstance(p, dict) or not isinstance(p.get("thread_id"), str):
            anomalies.append("prior entry %d of %s skipped (not an object or "
                             "thread_id not a string)" % (i, path))
            continue
        if isinstance(p.get("resolved"), bool):
            state = "resolved" if p["resolved"] else "open"
        elif p.get("state") in ("resolved", "open"):
            state = p["state"]
        else:
            state = "open"     # unknown state: conservative carry-forward (reply)
        f = p.get("file_path")
        if not isinstance(f, str) or not f.strip():
            f = p.get("file") if isinstance(p.get("file"), str) else None
        lo = hi = None
        ln = p.get("line_number")
        if isinstance(ln, int) and not isinstance(ln, bool):
            lo = hi = ln
        lr = p.get("line_range")
        if valid_range(lr):
            lo, hi = min(lr), max(lr)
        priors.append({"thread_id": p["thread_id"], "state": state,
                       "file": norm_path(f), "lo": lo, "hi": hi})
    return priors


def match_prior(priors, file, lo, hi):
    """First prior at the same locus; several matches -> lexicographically
    smallest thread_id (determinism)."""
    if file is None or lo is None:
        return None
    best = None
    for p in priors:
        if p["file"] == file and overlaps(p["lo"], p["hi"], lo, hi):
            if best is None or p["thread_id"] < best["thread_id"]:
                best = p
    if best is None:
        return None
    return {"thread_id": best["thread_id"], "state": best["state"]}


def finalize(groups, n_files, priors, threshold, sim):
    clusters = []
    for idx, grp in enumerate(groups, 1):
        entries = grp["entries"]
        rep = pick_rep(entries)
        merged = len(entries) > 1 and all(
            near_identical(a, b, sim) for a, b in combinations(entries, 2))
        agents = []
        for e in entries:
            a = str(e.get("agent", ""))
            if a not in agents:
                agents.append(a)
        cats = []
        for e in entries:
            nc = norm_cat(e.get("category"))
            if nc not in cats:
                cats.append(nc)
        rep_file = rep.get("file") if isinstance(rep.get("file"), str) else None
        if grp["file"] is None:
            locus = {"file": None, "lines": None, "anchor": "MR-process"}
        elif grp["lo"] is None:
            locus = {"file": rep_file, "lines": None, "anchor": rep_file}
        else:
            locus = {"file": rep_file, "lines": [grp["lo"], grp["hi"]],
                     "anchor": "%s:%d" % (rep_file, rep["line_range"][0])}
        reasons = []
        prior_m = match_prior(priors, grp["file"], grp["lo"], grp["hi"])
        if prior_m is not None:
            reasons.append(REASON_ALREADY)
        ranks = [SEV_RANK[e["severity"]] for e in entries
                 if e.get("severity") in SEV_RANK]
        if len(ranks) >= 2 and max(ranks) - min(ranks) >= 2:
            reasons.append(REASON_CONFLICT)
        if len(cats) > 1:
            reasons.append(REASON_CROSS)
        if len(entries) > 1 and not merged:
            reasons.append(REASON_SAME_LOCUS)
        if any(CONFIDENCE_FLOOR <= e["confidence"] < threshold for e in entries):
            reasons.append(REASON_SUB)
        auto = ((len(entries) == 1 or merged)
                and rep["confidence"] >= threshold
                and not reasons and prior_m is None)
        c = {
            "cluster_id": "c%03d" % idx,
            "locus": locus,
            "agents": agents,
            "corroboration": {"count": len(agents), "of": n_files, "agents": agents},
            "merged": merged,
            "categories": cats,
            "entries": [strip_internal(e) for e in entries],
            "posting": "auto" if auto else "worklist",
            "worklist_reasons": reasons,
        }
        if prior_m is not None:
            c["prior_match"] = prior_m
        clusters.append(c)
    return clusters


def fmt_val(v):
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def render_entry(e):
    return ["- %s: %s" % (k, fmt_val(v)) for k, v in e.items()]


def render_item(heading, entries):
    lines = ["### %s" % heading, ""]
    for e in entries:
        lines.extend(render_entry(e))
        lines.append("")
    return lines


def section_for(c):
    for reason, sec in SECTION_FOR_REASON:
        if reason in c["worklist_reasons"]:
            return sec
    return SEC_SUB


def render_worklist(clusters):
    """Render worklist.md: title, the 5 adjudication sections in single-pass
    consumption order (sub-threshold ordered by confidence desc), then the
    auto-cluster list. Every non-auto item quotes its entries verbatim inline."""
    auto_clusters = [c for c in clusters if c["posting"] == "auto"]
    sections = {sec: [] for _, sec in SECTION_FOR_REASON}
    for c in clusters:
        if c["posting"] == "worklist":
            sections[section_for(c)].append(c)

    lines = [WORKLIST_TITLE, ""]
    for _, sec in SECTION_FOR_REASON:
        lines.append(sec)
        items = sections[sec]
        if sec == SEC_SUB:
            items = sorted(items, key=lambda c: (
                -max(e["confidence"] for e in c["entries"]), c["cluster_id"]))
        if not items:
            lines.append("(none)")
        else:
            for c in items:
                lines.extend(render_block(sec, c))
        lines.append("")
    lines.append(SEC_AUTO)
    if auto_clusters:
        for c in auto_clusters:
            rep = max(c["entries"], key=lambda e: e["confidence"])
            lines.append("- %s — %s — rep confidence %d — %s" % (
                c["cluster_id"], c["locus"]["anchor"], rep["confidence"],
                fmt_val(rep.get("one_liner", ""))))
    else:
        lines.append("(none)")
    lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_block(sec, c):
    if sec == SEC_ALREADY:
        pm = c["prior_match"]
        if pm["state"] == "open":
            head = ("%s — reply on thread %s (open prior — carry the finding forward "
                    "as a reply on this recorded thread, never a new one)"
                    % (c["cluster_id"], pm["thread_id"]))
        else:
            head = ("%s — skip re-posting (resolved prior at this locus — the "
                    "position is already established)" % c["cluster_id"])
    elif sec == SEC_CONFLICT:
        head = ("%s — %s — Needs Human Judgment (both perspectives verbatim; never "
                "silently resolve)" % (c["cluster_id"], c["locus"]["anchor"]))
    elif sec == SEC_CROSS:
        head = "%s — %s (keep both perspectives)" % (c["cluster_id"],
                                                     c["locus"]["anchor"])
    elif sec == SEC_SAME_LOCUS:
        head = ("%s — %s (not near-identical — keep each perspective; decide each "
                "entry's inclusion from its quoted evidence)"
                % (c["cluster_id"], c["locus"]["anchor"]))
    else:
        head = "%s — %s — highest entry confidence %d" % (
            c["cluster_id"], c["locus"]["anchor"],
            max(e["confidence"] for e in c["entries"]))
    return render_item(head, c["entries"])


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic consolidation of validated findings files.")
    ap.add_argument("--findings", nargs="+", required=True,
                    help="1 to 3 validated findings JSON files")
    ap.add_argument("--prior", default=None,
                    help="optional prior-findings JSON (retrospective guard)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                    help="posting threshold on agent-assigned scores (default 70)")
    ap.add_argument("--similarity", type=float, default=DEFAULT_SIMILARITY,
                    help="near-identical Jaccard threshold (default 0.30)")
    a = ap.parse_args(argv)
    if not 1 <= len(a.findings) <= 3:
        ap.error("--findings takes 1 to 3 files")

    anomalies = []
    findings = load_findings(a.findings, anomalies)
    priors = load_priors(a.prior, anomalies) if a.prior else []

    groups = build_groups(findings)
    clusters = finalize(groups, len(a.findings), priors, a.threshold, a.similarity)

    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, "clusters.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(clusters, indent=2, ensure_ascii=False) + "\n")
    with open(os.path.join(a.out_dir, "worklist.md"), "w", encoding="utf-8") as fh:
        fh.write(render_worklist(clusters))

    st = {"clusters": len(clusters),
          "merged": sum(1 for c in clusters if c["merged"]),
          "worklist_items": sum(1 for c in clusters if c["posting"] == "worklist"),
          "auto_post": sum(1 for c in clusters if c["posting"] == "auto"),
          "already_adjudicated": sum(1 for c in clusters if "prior_match" in c),
          "anomalies": len(anomalies)}
    print(json.dumps(st))
    for an in anomalies:
        print("omni_consolidate: %s" % an, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
