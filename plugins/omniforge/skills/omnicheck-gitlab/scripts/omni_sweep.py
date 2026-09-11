#!/usr/bin/env python3
"""Push sweep intelligence (P2) — the sweep's brain, engine-independent.

Everything deterministic about "did this push fix the findings, and is there
new work?" lives here: delta partition, line-map re-anchoring, evidence
selection, the batched model call (ONE call per sweep), the citation rule,
and the renderers (living report + breadcrumb). The vocabulary is P1's
omni_verdict module, reused verbatim — never a parallel one.

Call-path policy (operator decision, WP0): the ONLY live provider tonight is
a `claude -p` spawn; DirectProviderAPI is an interface stub and touches no
secrets. Wiring to the engine trigger/queue is E2/E3 (the seam), not here.

Zero network writes: nothing in this module posts anything, ever. Posting is
the seam session's job.
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omni_verdict  # noqa: E402 — same directory, vocabulary reused verbatim

# ── Evidence caps (oversized-delta protection) ────────────────────────────

MAX_HUNKS_PER_FINDING = 8
MAX_HUNK_EXCERPT_LINES = 40
MAX_PACKET_CHARS = 20000

DOCS_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
CI_PATHS = (".gitlab-ci.yml", ".github/workflows/", "Jenkinsfile",
            ".circleci/", ".travis.yml")
MANIFEST_NAMES = ("pyproject.toml", "package.json", "requirements.txt",
                  "requirements-dev.txt", "Cargo.toml", "go.mod",
                  "setup.py", "setup.cfg", "Gemfile", "pom.xml")
MIGRATION_MARKERS = ("/migrations/", "/db/migrate/", "alembic/")

_TEST_PATH = omni_verdict._TEST_PATH  # same test-path rule as the model
_DOCS_PATH = omni_verdict._DOCS_PATH

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_SYMBOL_CANDIDATE = re.compile(r"`([^`\s]{2,})`|([A-Za-z_][A-Za-z0-9_]{4,})")
_SYMBOL_STOPLIST = {
    "the", "and", "for", "with", "this", "that", "from", "should", "would",
    "could", "missing", "never", "still", "when", "then", "here", "there",
    "line", "lines", "file", "files", "code", "test", "tests", "docs",
    "instead", "because", "return", "class", "import", "true", "false",
    "none", "null", "assert", "where", "which", "while", "these", "those",
    "your", "their", "into", "onto", "over", "under", "after", "before",
    "being", "been", "have", "has", "had", "will", "shall", "must", "does",
    "done", "made", "make", "like", "just", "also", "only", "very", "much",
    "more", "most", "some", "any", "all", "one", "two", "new", "old", "add",
    "added", "remove", "removed", "change", "changed", "uses", "using",
    "used", "about", "above", "below", "what", "whom", "who", "how", "why",
    "not", "but", "can", "may", "might", "was", "were", "are", "its",
    "itself", "json", "yaml", "path", "paths", "temp", "tmp", "dir",
    "directory", "token", "tokens", "body", "note", "notes", "data",
    "value", "values", "name", "names", "call", "calls", "multiple",
    "single", "every", "given", "gives", "corrupt", "races", "agents",
    "agent", "isolation", "isolate", "consolidate", "writes", "write",
    "reading", "writes", "improvised", "step", "steps",
}


# ── Delta parsing ─────────────────────────────────────────────────────────

def parse_hunks(diff_text):
    """Parse a unified diff into hunk records.

    Each hunk: {file, old_start, old_count, new_start, new_count, id,
    lines (raw body lines), adds, dels, new_file, deleted_file,
    renamed_to}. Hunk ids are `path:H<n>` — the citation currency.
    """
    hunks = []
    current_file = None
    file_flags = {}
    file_hunk_no = 0
    lines = (diff_text or "").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            m = re.match(r'^diff --git a/(.+?) b/(.+)$', line)
            current_file = m.group(2) if m else None
            file_flags = {"new_file": False, "deleted_file": False,
                          "renamed_to": None}
            file_hunk_no = 0
            # consume headers until a hunk or the next diff
            i += 1
            while i < len(lines) and not lines[i].startswith("@@") \
                    and not lines[i].startswith("diff --git "):
                h = lines[i]
                if h.startswith("new file mode"):
                    file_flags["new_file"] = True
                elif h.startswith("deleted file mode"):
                    file_flags["deleted_file"] = True
                elif h.startswith("rename to "):
                    file_flags["renamed_to"] = h[len("rename to "):].strip()
                i += 1
            continue
        m = _HUNK_HEADER.match(line)
        if m and current_file is not None:
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            body = []
            i += 1
            adds = dels = 0
            while i < len(lines) and not lines[i].startswith("@@") \
                    and not lines[i].startswith("diff --git "):
                l = lines[i]
                if l.startswith("+"):
                    adds += 1
                elif l.startswith("-"):
                    dels += 1
                body.append(l)
                i += 1
            file_hunk_no += 1
            hunks.append({
                "file": current_file,
                "old_start": old_start, "old_count": old_count,
                "new_start": new_start, "new_count": new_count,
                "id": "%s:H%d" % (current_file, file_hunk_no),
                "lines": body, "adds": adds, "dels": dels,
                "new_file": file_flags["new_file"],
                "deleted_file": file_flags["deleted_file"],
                "renamed_to": file_flags["renamed_to"],
            })
            continue
        i += 1
    return hunks


def _file_shape(path, hunk=None):
    """Shape tags for one file: lifecycle tags (new file / deleted) plus the
    content class (tests-only / ci / manifest / migration / docs / code)."""
    tags = set()
    if hunk and hunk.get("deleted_file"):
        tags.add("deleted")
    if hunk and hunk.get("new_file"):
        tags.add("new file")
    if _TEST_PATH.search(path):
        tags.add("tests-only")
    elif any(p in path for p in CI_PATHS) or path in CI_PATHS:
        tags.add("ci")
    elif os.path.basename(path) in MANIFEST_NAMES:
        tags.add("manifest")
    elif any(m in path for m in MIGRATION_MARKERS):
        tags.add("migration")
    elif _DOCS_PATH.search(path):
        tags.add("docs")
    else:
        tags.add("code")
    return tags


def _finding_symbols(finding):
    """Identifier candidates from the finding body, for the total-delta
    symbol search that catches cross-file fixes. Backticked tokens plus
    plain identifiers, minus English/code stop-words."""
    body = finding.get("body", "") or ""
    symbols = set()
    for m in _SYMBOL_CANDIDATE.finditer(body[:2000]):
        token = (m.group(1) or m.group(2) or "").strip("`")
        if token and token.lower() not in _SYMBOL_STOPLIST:
            symbols.add(token)
    return sorted(symbols)[:24]


_HUNK_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def _hunk_touches_finding(hunk, finding):
    if hunk["file"] == finding.get("file_path"):
        return True
    changed = "\n".join(l for l in hunk["lines"]
                        if l.startswith(("+", "-")))
    tokens = set(_HUNK_TOKEN.findall(changed))
    for sym in _finding_symbols(finding):
        if sym in tokens:
            return True
        if len(sym) >= 4 and any(
                (sym in t or t in sym) for t in tokens if len(t) >= 4):
            return True
    return False


def partition_delta(diff_text, findings):
    """Split the delta's hunks into finding-relevant vs residual.

    Relevant: touches an open finding's anchor file, or matches one of the
    finding's symbols anywhere in the delta (cross-file fixes). Residual:
    everything else — the new-work inventory (files, additions, deletions,
    shapes).
    """
    hunks = parse_hunks(diff_text)
    relevant, residual_hunks = [], []
    for hunk in hunks:
        if any(_hunk_touches_finding(hunk, f) for f in findings):
            relevant.append(hunk)
        else:
            residual_hunks.append(hunk)

    residual_files = []
    seen = set()
    additions = deletions = 0
    shapes = set()
    for hunk in residual_hunks:
        path = hunk["renamed_to"] or hunk["file"]
        if path not in seen:
            seen.add(path)
            residual_files.append(path)
            shapes |= _file_shape(path, hunk)
        additions += hunk["adds"]
        deletions += hunk["dels"]

    return {
        "relevant_hunks": relevant,
        "residual": {
            "files": residual_files,
            "additions": additions,
            "deletions": deletions,
            "shapes": sorted(shapes),
        },
    }


# ── Re-anchoring ──────────────────────────────────────────────────────────

def reanchor(finding, diff_text):
    """Re-anchor one finding's locus through the delta's line map.

    The finding's line_number is its position at review time — the OLD side
    of the reviewed_head..head delta. Deleted file / deleted locus ⇒
    obsolete candidate; renames carry the new path.
    """
    hunks = parse_hunks(diff_text)
    path = finding.get("file_path")
    line = finding.get("line_number")
    if path is None or line is None:
        # unanchored finding (process/general): nothing to re-anchor
        return {"status": "reanchored", "file_path": path,
                "line_number": line, "reason": ""}

    # rename resolution: the finding's file may have been renamed
    file_hunks = [h for h in hunks
                  if h["file"] == path or (h["renamed_to"] == path)]
    if not file_hunks and hunks:
        renamed = {h["renamed_to"]: h for h in hunks if h.get("renamed_to")}
        # reverse lookup: finding names the OLD path; find hunk whose old side is it
        for h in hunks:
            if h.get("renamed_to") and _old_path_of(diff_text, h) == path:
                file_hunks = [h]
                break

    if not file_hunks:
        return {"status": "reanchored", "file_path": path,
                "line_number": line, "reason": ""}

    target = file_hunks[0]
    new_path = target["renamed_to"] or path
    if target["deleted_file"]:
        return {"status": "obsolete", "file_path": path, "line_number": None,
                "reason": "file deleted in delta"}

    offset = 0
    for h in sorted(file_hunks, key=lambda x: x["old_start"]):
        old_lo, old_hi = h["old_start"], h["old_start"] + h["old_count"] - 1
        if line < old_lo:
            return {"status": "reanchored", "file_path": new_path,
                    "line_number": line + offset, "reason": ""}
        if line <= old_hi:
            # inside the hunk: per-line walk for exact mapping
            old_ln, new_ln = h["old_start"], h["new_start"]
            for l in h["lines"]:
                if l.startswith("+"):
                    new_ln += 1
                elif l.startswith("-"):
                    if old_ln == line:
                        return {"status": "obsolete",
                                "file_path": new_path, "line_number": None,
                                "reason": "anchor locus deleted in delta"}
                    old_ln += 1
                else:
                    if old_ln == line:
                        return {"status": "reanchored", "file_path": new_path,
                                "line_number": new_ln, "reason": ""}
                    old_ln += 1
                    new_ln += 1
            return {"status": "reanchored", "file_path": new_path,
                    "line_number": new_ln, "reason": ""}
        offset += h["new_count"] - h["old_count"]
    return {"status": "reanchored", "file_path": new_path,
            "line_number": line + offset, "reason": ""}


def _old_path_of(diff_text, hunk):
    """Old-side path for a renamed hunk (from the diff header)."""
    for m in re.finditer(r"diff --git a/(.+?) b/(.+)", diff_text):
        if m.group(2) == hunk["file"]:
            return m.group(1)
    return None


def stale_markings(findings):
    """Non-ancestor delta (rebase/force-push): no re-anchoring — mark stale,
    report-only, recommend a full re-review (rev-3 rule)."""
    return {f["id"]: {"status": "stale",
                      "reason": "non-ancestor delta: locus unverifiable, "
                                "full re-review recommended"}
            for f in findings}


# ── Evidence packets + citation rule ──────────────────────────────────────

def build_evidence_packets(diff_text, findings, anchor_map):
    """One evidence packet per finding: {concern, severity, kind, locus,
    candidate_hunks (capped), no_evidence}. Findings with no relevant hunks
    stay in the batch with the no-evidence marker — every finding gets a
    verdict."""
    hunks = parse_hunks(diff_text)
    packets = []
    budget = MAX_PACKET_CHARS
    for f in findings:
        anchor = anchor_map.get(f["id"], {})
        if anchor.get("status") == "obsolete":
            selected = []
        else:
            anchor_file = anchor.get("file_path") or f.get("file_path")
            selected = [h for h in hunks if h["file"] == anchor_file]
            if not selected:  # symbol search across the whole delta
                selected = [h for h in hunks
                            if _hunk_touches_finding(h, f)]
        selected = selected[:MAX_HUNKS_PER_FINDING]
        capped = []
        for h in selected:
            excerpt = h["lines"][:MAX_HUNK_EXCERPT_LINES]
            capped.append({"id": h["id"], "file": h["file"],
                           "old_start": h["old_start"],
                           "new_start": h["new_start"],
                           "lines": excerpt})
        packet = {
            "finding_id": f["id"],
            "concern": (f.get("body", "") or "")[:400],
            "severity": f.get("severity", "minor"),
            "kind": f.get("kind", "code"),
            "locus": {"file": anchor.get("file_path") or f.get("file_path"),
                      "line": anchor.get("line_number", f.get("line_number")),
                      "status": anchor.get("status", "reanchored")},
            "candidate_hunks": capped,
            "no_evidence": not capped,
        }
        packets.append(packet)
        budget -= len(json.dumps(packet))
        if budget <= 0:
            packet["candidate_hunks"] = []
            packet["no_evidence"] = True
            packet["evidence_truncated"] = True
            budget = 1  # remaining packets carry the marker too
    return packets


def apply_citation_rule(verdicts, packets):
    """A `fixed` verdict must cite a hunk id from the finding's supplied
    evidence set; anything else downgrades to needs_judgment. The model can
    never close a finding on an assertion alone."""
    valid_ids = {p["finding_id"]: {h["id"] for h in p["candidate_hunks"]}
                 for p in packets}
    out = []
    for v in verdicts:
        v = dict(v)
        if v.get("verdict") == "fixed":
            cited = v.get("evidence_hunk_id")
            if not cited or cited not in valid_ids.get(v.get("finding_id"),
                                                       set()):
                v["verdict"] = "needs_judgment"
                v["reason"] = ("downgraded: fixed verdict cited no hunk id "
                               "from the supplied evidence set (citation "
                               "rule)")
        out.append(v)
    return out


# ── Skip conditions ───────────────────────────────────────────────────────

def sweep_skip_decision(diff, findings, tree_hash_equal):
    """SKIP_* when the sweep (or its expensive legs) should not run.

    - SKIP_EMPTY_DELTA / SKIP_TREE_HASH_EQUAL: nothing to do at all.
    - SKIP_EVALUATION_DOCS_CI_ONLY: delta exists but is docs/CI-only with
      nothing open to verify — render the one-line residual report, skip the
      model call (rev-3's recipe table: "sweep runs, residual trivial, one
      report line, no review" — the AI leg is what gets skipped).
    """
    if tree_hash_equal:
        return "SKIP_TREE_HASH_EQUAL"
    hunks = parse_hunks(diff)
    if not hunks:
        return "SKIP_EMPTY_DELTA"
    if not findings:
        shapes = set()
        for h in hunks:
            shapes |= _file_shape(h["renamed_to"] or h["file"], h)
        if shapes <= {"docs", "ci", "new file", "deleted"}:
            return "SKIP_EVALUATION_DOCS_CI_ONLY"
    return None


# ── Providers (call-path policy: claude -p spawn only, direct API = stub) ─

class DirectProviderAPI:
    """Interface stub. Direct provider calls need a secrets/retry story that
    is deliberately out of scope tonight (WP0 decision) — implementing this
    is a supervised-session task. Touches no tokens."""

    def call(self, prompt):
        raise NotImplementedError(
            "DirectProviderAPI is a stub by design: the claude -p spawn "
            "(ClaudeSpawnProvider) is the only live call path.")


class ClaudeSpawnProvider:
    """ONE `claude -p` spawn per sweep carrying the whole batched payload."""

    def __init__(self, timeout=300):
        self.timeout = timeout

    def build_command(self, prompt):
        return ["claude", "-p", prompt, "--output-format", "text"]

    def call(self, prompt):
        r = subprocess.run(self.build_command(prompt), capture_output=True,
                           text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError("claude -p failed: %s" % r.stderr[:300])
        return r.stdout

    @staticmethod
    def parse_verdicts(text):
        """Extract the JSON array from the model's reply (tolerates prose)."""
        for m in re.finditer(r"\[.*\]", text, re.DOTALL):
            try:
                data = json.loads(m.group(0))
                if isinstance(data, list) and data \
                        and isinstance(data[0], dict) \
                        and "finding_id" in data[0]:
                    return data
            except json.JSONDecodeError:
                continue
        raise ValueError("no verdict JSON array found in model reply")


# ── Renderers ─────────────────────────────────────────────────────────────

def _short(sha):
    return (sha or "")[:8]


def render_breadcrumb(head, sweep_number, verdicts, findings, residual,
                      delta_review_queued, reviewed_head):
    counts = {"fixed": 0, "not_fixed": 0, "needs_judgment": 0, "obsolete": 0,
              "stale": 0}
    open_desc = []
    sev_by_id = {f["id"]: f.get("severity", "minor") for f in findings}
    for v in verdicts:
        key = v.get("verdict", "needs_judgment")
        counts[key] = counts.get(key, 0) + 1
        if key in ("not_fixed", "needs_judgment"):
            open_desc.append("%s %s" % (v.get("finding_id", "?"),
                                        sev_by_id.get(v.get("finding_id"),
                                                      "").capitalize()
                                        or "finding"))
    lines = [
        "**Push check complete — head `%s`** (sweep #%d)" % (
            _short(head), sweep_number),
        "",
        "- Fixed since review: %d finding%s" % (
            counts["fixed"], "s" if counts["fixed"] != 1 else ""),
    ]
    still_open = counts["not_fixed"]
    if still_open:
        lines.append("- Still open: %d (%s)" % (
            still_open, ", ".join(open_desc[:5])))
    if counts["needs_judgment"]:
        lines.append("- Needs judgment: %d" % counts["needs_judgment"])
    if counts["obsolete"]:
        lines.append("- Obsolete (locus rewritten): %d" % counts["obsolete"])
    if counts["stale"]:
        lines.append("- Stale (non-ancestor delta): %d" % counts["stale"])
    if residual and residual.get("files"):
        new_work = "- New work: %d file%s / %d lines since `%s`" % (
            len(residual["files"]),
            "s" if len(residual["files"]) != 1 else "",
            residual.get("additions", 0), _short(reviewed_head))
        if delta_review_queued:
            new_work += " → delta review queued"
        lines.append(new_work)
    lines += [
        "",
        "Full verification table: the report above (updated in place).",
    ]
    return "\n".join(lines)


def render_report(head, reviewed_head, findings, anchor_map, verdicts,
                  residual):
    by_id = {v.get("finding_id"): v for v in verdicts}
    lines = [
        "**Push sweep report** — `%s..%s`" % (
            _short(reviewed_head), _short(head)),
        "",
        "| finding | severity | kind | verdict | evidence | note |",
        "|---|---|---|---|---|---|",
    ]
    for f in findings:
        v = by_id.get(f["id"], {})
        anchor = anchor_map.get(f["id"], {})
        verdict = v.get("verdict", "needs_judgment")
        if anchor.get("status") == "obsolete":
            verdict = "obsolete"
        if anchor.get("status") == "stale":
            verdict = "stale"
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            f["id"], f.get("severity", "minor"), f.get("kind", "code"),
            verdict, v.get("evidence_hunk_id", "—"),
            (v.get("one_line") or v.get("reason")
             or anchor.get("reason") or "").replace("|", "/")[:80]))
    if residual and residual.get("files"):
        lines += [
            "",
            "### Residual new work (unreviewed since `%s`)" % _short(
                reviewed_head),
            "",
            "- Files: %s" % ", ".join(residual["files"][:20]),
            "- Size: +%d / −%d" % (residual.get("additions", 0),
                                   residual.get("deletions", 0)),
            "- Shapes: %s" % ", ".join(residual.get("shapes", [])),
        ]
    else:
        lines += ["", "### Residual new work: none"]
    return "\n".join(lines)


# ── Sweep orchestration (offline; zero network writes) ────────────────────

def _git(repo_root, *args):
    r = subprocess.run(["git", "-C", repo_root, *args], capture_output=True,
                       text=True, timeout=60)
    return r.returncode, r.stdout


def run_sweep(repo_root, reviewed_head, head, findings, provider,
              sweep_number=1, dry_run=False, delta_review_queued=False):
    """Run one sweep. Returns skip reason (or None), verdicts, partition,
    and the rendered report + breadcrumb. Never posts anything."""
    # Ancestry: rebase/force-push => stale markings, report-only
    rc, _ = _git(repo_root, "merge-base", "--is-ancestor", reviewed_head, head)
    non_ancestor = rc != 0

    # Tree-hash equality: merge/rebase-only push => skip entirely
    _, tree_reviewed = _git(repo_root, "rev-parse", reviewed_head + "^{tree}")
    _, tree_head = _git(repo_root, "rev-parse", head + "^{tree}")
    tree_equal = tree_reviewed.strip() == tree_head.strip() and \
        tree_reviewed.strip() != ""

    _, diff_text = _git(repo_root, "diff", reviewed_head + ".." + head)

    skip = sweep_skip_decision(diff_text, findings, tree_equal)
    if skip in ("SKIP_EMPTY_DELTA", "SKIP_TREE_HASH_EQUAL"):
        return {"skip": skip, "verdicts": [], "findings": [],
                "residual": None, "report": "", "breadcrumb": "",
                "relevant_files": []}

    if non_ancestor:
        anchor_map = stale_markings(findings)
        verdicts = [{"finding_id": f["id"], "verdict": "stale",
                     "reason": anchor_map[f["id"]]["reason"]}
                    for f in findings]
        partition = partition_delta(diff_text, [])
    else:
        anchor_map = {f["id"]: reanchor(f, diff_text) for f in findings}
        # Obsolete loci never reach the model (there is nothing left to
        # cite) — they carry a synthesized obsolete verdict so the report
        # and breadcrumb still account for them.
        obsolete_ids = {fid for fid, a in anchor_map.items()
                        if a["status"] == "obsolete"}
        open_findings = [f for f in findings if f["id"] not in obsolete_ids]
        partition = partition_delta(diff_text, open_findings)

        verdicts = [{"finding_id": fid, "verdict": "obsolete",
                     "reason": anchor_map[fid]["reason"]}
                    for fid in sorted(obsolete_ids)]
        if skip == "SKIP_EVALUATION_DOCS_CI_ONLY":
            pass  # nothing open, docs/CI-only residual: no model call
        elif dry_run:
            verdicts += [{"finding_id": f["id"],
                          "verdict": "needs_judgment",
                          "reason": "dry run: no model call"}
                         for f in open_findings]
        else:
            packets = build_evidence_packets(diff_text, open_findings,
                                             anchor_map)
            prompt = build_batch_prompt(packets)
            reply = provider.call(prompt)
            verdicts += ClaudeSpawnProvider.parse_verdicts(reply)
            verdicts = apply_citation_rule(verdicts, packets)

    relevant_files = sorted({h["file"] for h in partition["relevant_hunks"]})
    residual = partition["residual"]
    report = render_report(head, reviewed_head, findings, anchor_map,
                           verdicts, residual)
    breadcrumb = render_breadcrumb(
        head, sweep_number, verdicts, findings, residual,
        delta_review_queued, reviewed_head)
    return {
        "skip": None,
        "verdicts": verdicts,
        "findings": findings,
        "anchor_map": anchor_map,
        "residual": residual,
        "relevant_files": relevant_files,
        "report": report,
        "breadcrumb": breadcrumb,
    }


def build_batch_prompt(packets):
    """The ONE batched structured call per sweep: all findings, capped
    candidate hunks, JSON reply contract (template:
    ../references/sweep-prompt.md)."""
    payload = json.dumps(packets, indent=1)
    return (
        "You are the sweep adjudicator. For EVERY finding below, decide: "
        "given the concern and its candidate hunks from the delta, is the "
        "concern fixed at the new head?\n\n"
        "Reply with a JSON array ONLY — one object per finding:\n"
        '[{"finding_id": "...", "verdict": "fixed"|"not_fixed"|'
        '"needs_judgment", "evidence_hunk_id": "<candidate hunk id or null>", '
        '"confidence": <int 50-100>, "one_line": "<one sentence>"}]\n\n'
        "Rules:\n"
        "- A `fixed` verdict MUST cite an evidence_hunk_id from that "
        "finding's candidate_hunks; uncited fixed verdicts are downgraded.\n"
        "- Findings marked no_evidence have no candidate hunks: answer "
        "not_fixed or needs_judgment — never fixed.\n"
        "- Stay grounded in the hunks; do not speculate.\n\n"
        "Findings:\n" + payload)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--reviewed-head", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--findings", required=True,
                    help="JSON file: classified findings (omni_verdict "
                         "records) or {threads: [...]} to classify")
    ap.add_argument("--model", choices=("none", "claude-spawn"),
                    default="none")
    ap.add_argument("--sweep-number", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true",
                    help="no model call: every open finding comes back "
                         "needs_judgment with a stated reason")
    ap.add_argument("--out-report", default="")
    ap.add_argument("--out-breadcrumb", default="")
    args = ap.parse_args(argv or sys.argv[1:])

    payload = json.load(open(args.findings))
    if isinstance(payload, dict) and "threads" in payload:
        findings, _ = omni_verdict.classify_run(payload["threads"])
    else:
        findings = payload if isinstance(payload, list) else payload.get(
            "findings", [])
    provider = ClaudeSpawnProvider() if args.model == "claude-spawn" \
        else DirectProviderAPI()

    result = run_sweep(args.repo_root, args.reviewed_head, args.head,
                       findings, provider, sweep_number=args.sweep_number,
                       dry_run=args.dry_run or args.model == "none")
    print(json.dumps({k: v for k, v in result.items()
                      if k != "findings"}, indent=1))
    if args.out_report and result["report"]:
        open(args.out_report, "w").write(result["report"] + "\n")
    if args.out_breadcrumb and result["breadcrumb"]:
        open(args.out_breadcrumb, "w").write(result["breadcrumb"] + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
