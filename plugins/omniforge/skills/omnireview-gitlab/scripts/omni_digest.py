#!/usr/bin/env python3
"""omni_digest.py — build the ONE-FILE Phase-4 context digest from the saved
Phase-1 tool outputs (fetch_mr_data JSON + fetch_mr_discussions JSON).

Bot-authored artifacts (OmniForge summary notes, posted finding-thread
bodies, verdict tokens, findings file:line anchors, thread IDs, resolved
state) are carried VERBATIM — never summarized, never truncated (engine
HISTORY_RULE, run-review.sh:398-401). ONLY human discussion prose is capped
(500 chars = 400 head + marker + 60 tail) and re-carried diffs are reduced to
hunk headers + per-file counts. If the digest still exceeds the 8,000-char
budget, the OLDEST threads' prose is dropped entirely (thread ID + resolved
state always remain — machine fields). Pure string slicing and counting —
no LLM calls, no summarization of any content, ever.

Outputs: digest.md (the Phase-4 one-file input) and prior-findings.json
(the omni_consolidate.py --prior input) into --out-dir, or the explicit
--out / --prior-out paths. Stdout: exactly one JSON line. Diagnostics:
stderr. Stdlib only.

CLI:
  python3 omni_digest.py <fetch_mr_data.json> [<mr_discussions.json>] \
      [--out-dir DIR] [--out FILE] [--prior-out FILE]

Every bad-discussions mode DEGRADES — one stderr warning line,
retrospective: false, prior_findings: [], exit 0 — never a hard failure:
second positional arg missing (nargs="?"), file absent, invalid JSON,
success != true, missing `discussions` key. Only an unreadable/invalid
mr_data file is a usage error (exit 2).
"""

import argparse
import json
import os
import sys

OMNIFORGE_HEADER = "## OmniForge"
PROSE_CAP, HEAD, TAIL = 500, 400, 60
BUDGET = 8000

DIGEST_NAME = "digest.md"
PRIOR_NAME = "prior-findings.json"

DIFF_SECTION_HEAD = ("## Diff re-carry (hunk headers + per-file counts only "
                     "— full diff stays at the Phase-1 temp path)")


def truncate_prose(text):
    """Cap human prose at PROSE_CAP chars: HEAD + marker + TAIL."""
    if len(text) <= PROSE_CAP:
        return text
    n = len(text) - (HEAD + TAIL)
    return text[:HEAD] + "\n[…truncated %d chars…]\n" % n + text[-TAIL:]


def is_omniforge_note(body):
    """Content-based OmniForge detection: the summary-note header, or the
    posted-finding template (severity line + confidence + attribution)."""
    return body.lstrip().startswith(OMNIFORGE_HEADER) or (
        ("**Critical** — " in body or "**Important** — " in body
         or "**Minor** — " in body)
        and "Confidence: " in body and "Found by:" in body)


def note_bodies(d):
    """Ordered note bodies of one discussion thread. Handles both the raw
    GitLab shape (notes[]) and the fetch_mr_discussions MCP shape
    (body + replies[]); system notes are skipped."""
    out = []
    if isinstance(d.get("notes"), list) and d["notes"]:
        for n in d["notes"]:
            if isinstance(n, dict) and not n.get("system") \
                    and isinstance(n.get("body"), str):
                out.append(n["body"])
        return out
    if isinstance(d.get("body"), str):
        out.append(d["body"])
    for r in d.get("replies") or []:
        if isinstance(r, dict) and isinstance(r.get("body"), str):
            out.append(r["body"])
    return out


def thread_activity(d):
    """Newest created_at visible on the thread ('' when absent) — the
    newest-first sort key (lexicographic; ISO-8601 sorts correctly)."""
    stamps = []
    if isinstance(d.get("created_at"), str):
        stamps.append(d["created_at"])
    for group in (d.get("notes") or [], d.get("replies") or []):
        for n in group:
            if isinstance(n, dict) and isinstance(n.get("created_at"), str):
                stamps.append(n["created_at"])
    return max(stamps) if stamps else ""


def norm_threads(raw_threads):
    """Normalize the discussions list into the thread model:
    {thread_id, resolved, file, line, notes, activity}. Position data comes
    from position.new_path/new_line (raw shape) or file_path/line_number
    (MCP shape); threads WITHOUT position data keep file None — they simply
    never locus-match downstream, never crash."""
    threads = []
    for d in raw_threads:
        if not isinstance(d, dict):
            continue
        pos = d.get("position") if isinstance(d.get("position"), dict) else {}
        f = pos.get("new_path") or d.get("file_path") or d.get("new_path")
        if not isinstance(f, str) or not f.strip():
            f = None
        ln = pos.get("new_line")
        if not isinstance(ln, int) or isinstance(ln, bool):
            ln = d.get("line_number")
        if not isinstance(ln, int) or isinstance(ln, bool):
            ln = None
        tid = d.get("id")
        if not isinstance(tid, str):
            tid = str(tid)
        threads.append({
            "thread_id": tid,
            "resolved": bool(d.get("resolved", False)),
            "file": f,
            "line": ln,
            "notes": note_bodies(d),
            "activity": thread_activity(d),
        })
    # newest first (stable: equal activity keeps input order)
    threads.sort(key=lambda t: t["activity"], reverse=True)
    return threads


def locus_str(t):
    if t["file"] is None:
        return "no locus"
    if t["line"] is None:
        return t["file"]
    return "%s:%d" % (t["file"], t["line"])


def diff_recarry_lines(mr):
    """Per-file +added/-removed counts + hunk header lines ONLY — diff
    bodies are never re-carried. Prefers the raw `diff` string; falls back
    to diff_line_map (hunk headers synthesized as @@ +start,count @@)."""
    lines = []
    diff = mr.get("diff")
    if isinstance(diff, str) and diff.strip():
        counts, hunks, current = {}, {}, None
        for line in diff.split("\n"):
            if line.startswith("+++"):
                if line.startswith("+++ b/"):
                    current = line[6:]
                elif line.startswith("+++ /dev/null"):
                    current = None                # deleted file: no new side
                else:
                    # unprefixed header (+++ src/app.py); a trailing
                    # tab-separated timestamp is not part of the path
                    current = line[4:].split("\t")[0].strip() or None
                if current is not None:
                    counts.setdefault(current, [0, 0])
                    hunks.setdefault(current, [])
            elif line.startswith("@@") and current is not None:
                hunks[current].append(line)
            elif line.startswith("+") and current is not None:
                counts[current][0] += 1
            elif line.startswith("-") and not line.startswith("---") \
                    and current is not None:
                counts[current][1] += 1
        for path in counts:                      # insertion order = diff order
            lines.append("| %s | +%d/-%d |" % (path, counts[path][0],
                                               counts[path][1]))
            lines.extend(hunks.get(path, []))
    else:
        dlm = mr.get("diff_line_map")
        if isinstance(dlm, dict):
            for path, entry in dlm.items():
                if not isinstance(entry, dict):
                    continue
                added = len(entry.get("added_lines") or [])
                lines.append("| %s | +%d/-0 |" % (path, added))
                for h in entry.get("hunks") or []:
                    if isinstance(h, dict):
                        lines.append("@@ +%s,%s @@" % (h.get("new_start", 0),
                                                       h.get("new_count", 0)))
    return lines


def build_prior_findings(threads):
    """One entry per thread carrying >= 1 OmniForge-format note, in digest
    (newest-first) order. Shape = omni_consolidate.py load_priors' native
    input: {thread_id, file_path, line_number, body, resolved, state}."""
    priors = []
    for t in threads:
        body = next((b for b in t["notes"] if is_omniforge_note(b)), None)
        if body is None:
            continue
        priors.append({
            "thread_id": t["thread_id"],
            "file_path": t["file"],
            "line_number": t["line"],
            "body": body,                        # verbatim bot artifact
            "resolved": t["resolved"],
            "state": "resolved" if t["resolved"] else "open",
        })
    return priors


def build_thread_blocks(threads):
    """Per-thread render blocks: heading + note segments tagged bot (always
    verbatim) / prose (truncate_prose applies). Returns blocks and the count
    of threads whose prose was capped."""
    blocks, truncated = [], 0
    for t in threads:
        head = "## Thread %s — %s — %s" % (
            t["thread_id"], "resolved" if t["resolved"] else "unresolved",
            locus_str(t))
        segs, hit = [], False
        for body in t["notes"]:
            if is_omniforge_note(body):
                segs.append(("bot", body))
            else:
                if len(body) > PROSE_CAP:
                    hit = True
                segs.append(("prose", truncate_prose(body)))
        if hit:
            truncated += 1
        blocks.append({"head": head, "segs": segs})
    return blocks, truncated


def render(header, blocks, diff_lines):
    """Render the digest: chunks joined by blank lines, one trailing
    newline. The FINAL chunk's own trailing newlines are preserved exactly
    (bot artifacts are byte-verbatim) — only the separator is added, never
    a blanket rstrip."""
    chunks = [header]
    for b in blocks:
        chunks.append(b["head"])
        for kind, text in b["segs"]:
            if text is not None:                 # dropped by the budget pass
                chunks.append(text)
    if diff_lines:
        chunks.append(DIFF_SECTION_HEAD)
        chunks.append("\n".join(diff_lines))
    return "\n\n".join(chunks) + "\n"


def rendered_len(header, blocks, diff_lines):
    """Exact len() of render(...) without building the string: chunk sizes
    + one \\n\\n separator per chunk boundary + the final newline. Keeping
    this in lockstep with render() makes the budget pass pure arithmetic."""
    n = len(header) + 1                           # final "\n"
    for b in blocks:
        n += 2 + len(b["head"])
        for kind, text in b["segs"]:
            if text is not None:
                n += 2 + len(text)
    if diff_lines:
        n += 2 + len(DIFF_SECTION_HEAD)
        n += 2 + len("\n".join(diff_lines))
    return n


def enforce_budget(header, blocks, diff_lines, budget):
    """Drop the OLDEST prose (oldest thread first, notes in note order)
    until the rendered digest fits the budget; bot segments, thread
    headings, and machine fields always stay. Per-segment cost is computed
    ONCE and drops are arithmetic — the document renders exactly once at
    the end (never re-rendered per drop). When even dropping every prose
    segment cannot reach the budget (heading overhead alone is over), the
    pass exhausts the droppable bytes and returns the best-effort render.
    """
    total = rendered_len(header, blocks, diff_lines)
    if total <= budget:
        return render(header, blocks, diff_lines)
    need = total - budget                         # bytes to shed
    for b in reversed(blocks):                    # oldest thread first
        if need <= 0:
            break
        segs = []
        for kind, text in b["segs"]:
            if kind == "prose" and text is not None and need > 0:
                need -= 2 + len(text)             # chunk + its separator
                segs.append((kind, None))
            else:
                segs.append((kind, text))
        b["segs"] = segs
    return render(header, blocks, diff_lines)


def degrade(reason):
    print("omni_digest: %s — continuing with retrospective: false" % reason,
          file=sys.stderr)


def load_discussions(path):
    """Return the discussions list, or None after ONE warning when the
    input is missing/absent/invalid/not-successful/key-less (degrade)."""
    if path is None:
        degrade("no discussions input provided")
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as e:
        degrade("cannot read discussions file (%s)" % e)
        return None
    except ValueError as e:
        degrade("discussions file is not valid JSON (%s)" % e)
        return None
    if not isinstance(data, dict):
        degrade("discussions JSON is not an object envelope")
        return None
    if data.get("success") is not True:
        degrade("discussions envelope carries success != true")
        return None
    if not isinstance(data.get("discussions"), list):
        degrade("discussions envelope lacks the discussions list")
        return None
    return data["discussions"]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build the Phase-4 context digest + prior findings.")
    ap.add_argument("mr_data",
                    help="saved Phase-1 fetch_mr_data JSON")
    ap.add_argument("discussions", nargs="?", default=None,
                    help="saved Phase-1 fetch_mr_discussions JSON "
                         "(missing/absent/invalid degrades — never fails)")
    ap.add_argument("--out-dir", default=None,
                    help="directory for digest.md + prior-findings.json "
                         "(default: <mr_data dir>/omni_digest)")
    ap.add_argument("--out", default=None,
                    help="explicit digest.md path (overrides --out-dir)")
    ap.add_argument("--prior-out", default=None,
                    help="explicit prior-findings.json path")
    a = ap.parse_args(argv)

    try:
        with open(a.mr_data, encoding="utf-8") as fh:
            mr = json.load(fh)
    except OSError as e:
        print("omni_digest: cannot read mr_data: %s" % e, file=sys.stderr)
        return 2
    except ValueError as e:
        print("omni_digest: mr_data is not valid JSON: %s" % e,
              file=sys.stderr)
        return 2
    if not isinstance(mr, dict):
        print("omni_digest: mr_data must be a JSON object", file=sys.stderr)
        return 2

    raw_threads = load_discussions(a.discussions)
    diff_lines = diff_recarry_lines(mr)

    if raw_threads is None:
        threads = []
        priors = []
        blocks = []
        truncated = 0
        comments = mr.get("comments")
        if isinstance(comments, str) and comments.strip():
            blocks = [{"head": "## MR comments (from fetch_mr_data — "
                               "discussions input unavailable)",
                       "segs": [("prose", truncate_prose(comments))]}]
            if len(comments) > PROSE_CAP:
                truncated = 1
    else:
        threads = norm_threads(raw_threads)
        priors = build_prior_findings(threads)
        blocks, truncated = build_thread_blocks(threads)

    retrospective = len(priors) > 0
    header = "# MR digest — retrospective: %s" % (
        "true" if retrospective else "false")
    digest_text = enforce_budget(header, blocks, diff_lines, BUDGET)

    out_dir = a.out_dir or os.path.join(
        os.path.dirname(os.path.abspath(a.mr_data)), "omni_digest")
    digest_path = a.out or os.path.join(out_dir, DIGEST_NAME)
    prior_path = a.prior_out or os.path.join(out_dir, PRIOR_NAME)
    for path, content in (
            (digest_path, digest_text),
            (prior_path, json.dumps(
                {"retrospective": retrospective, "prior_findings": priors},
                indent=2, ensure_ascii=False) + "\n")):
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    print(json.dumps({
        "retrospective": retrospective,
        "prior_count": len(priors),
        "digest": digest_path,
        "prior_out": prior_path,
        "threads_total": len(threads),
        "threads_truncated": truncated,
        "digest_chars": len(digest_text),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
