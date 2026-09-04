#!/usr/bin/env python3
"""make_ab_prompts.py — deterministic 6-file reviewer-sim prompt slicer
(R2-D spec section 8.3, SECONDARY illustrative-only evidence; NO LLM
calls, NO dispatch — the 6 Agent runs are orchestrator work).

Slices gather["data"]["diff"] into per-file chunks (split on lines
starting `diff --git a/`; the new path is the `b/` side of the header)
and writes {old,new}_{analyst,codebase,security}.md — one file per
side x agent: a 3-line header (side / agent / N owned files), the
VERBATIM pinned sim instruction, then per owned file (canonical
partition order) a `### <path>` heading, the fenced diff body (or the
no-diff-body marker), and the `<path> added_lines: <n>` line from the
partition. Deterministic: same inputs -> byte-identical outputs.

The synthetic test gathers carry NO diff field (diff_line_map only) —
this tool is run against the ENGINE-LOCAL real gather (spec D7), whose
diff text is truncated; never copy real diff text into committed files.
Sim disclosures (spec section 8.3, mandatory wherever these walls are
quoted): the uniform-cost sim is circular for validating a non-uniform
cost model; single-pair wall differences inside the +/-75 s band are
noise and pooling is impractical locally; sim numbers carry NO
pass/fail or retune authority — the <=150 s REAL-skew target is
R2-E's release gate, not this loop's.
"""

import argparse
import json
import os
import sys

AGENTS = ("analyst", "codebase", "security")
NO_DIFF_BODY = "(no diff body available for %s)"

PINNED_INSTRUCTION = (
    "You are simulating a code reviewer. For each owned file below, "
    "read its diff and record exactly one line: `<path>: <added_lines> "
    "added lines, <k> risk markers` where k = count of '+' lines "
    "containing any of (password, token, secret, sql, auth, null, "
    "except, http), case-insensitive. Use no other tools, do not "
    "explore, do not read other files. Output all lines, then `Status: "
    "DONE`.")


def slice_diff(diff_text):
    """diff text -> {new-path: chunk body}.

    Chunks start at each `diff --git a/<old> b/<new>` header; the new
    path is taken from the ` b/` side of that header. Body lines are
    joined verbatim (trailing blank run trimmed); the header line itself
    is not part of the body.
    """
    chunks = {}
    path = None
    body = []
    for line in diff_text.split("\n"):
        if line.startswith("diff --git a/"):
            if path is not None:
                chunks[path] = "\n".join(body).rstrip("\n")
            rest = line[len("diff --git a/"):]
            idx = rest.find(" b/")
            path = rest[idx + 3:] if idx >= 0 else rest
            body = []
        elif path is not None:
            body.append(line)
    if path is not None:
        chunks[path] = "\n".join(body).rstrip("\n")
    return chunks


def _fence(body):
    """Fence longer than any backtick run inside the body (a diff that
    adds markdown fences must not break out of its own code block)."""
    longest = run = 0
    for ch in body:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return "`" * max(3, longest + 1)


def build_prompts(gather, old_partition, new_partition):
    """(gather, old partition.json, new partition.json) -> {filename: text}.

    Pure: no I/O, no clock, deterministic in the inputs.
    """
    chunks = slice_diff(gather.get("data", {}).get("diff") or "")
    out = {}
    for side, partition in (("old", old_partition), ("new", new_partition)):
        added = {f["path"]: f["added_lines"] for f in partition["files"]}
        for agent in AGENTS:
            owned = partition["agents"][agent]["files"]
            lines = ["side: %s" % side,
                     "agent: %s" % agent,
                     "owned files: %d" % len(owned),
                     "",
                     PINNED_INSTRUCTION]
            for path in owned:
                body = chunks.get(path)
                lines.append("")
                lines.append("### %s" % path)
                if body is None:
                    lines.append(NO_DIFF_BODY % path)
                else:
                    fence = _fence(body)
                    lines.append(fence)
                    lines.append(body)
                    lines.append(fence)
                lines.append("%s added_lines: %d"
                             % (path, added.get(path, 0)))
            out["%s_%s.md" % (side, agent)] = "\n".join(lines) + "\n"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic 6-file reviewer-sim prompt slicer "
                    "(spec section 8.3, illustrative-only — no LLM "
                    "calls, no dispatch)")
    ap.add_argument("--gather", required=True,
                    help="mr gather JSON (needs data.diff for slicing)")
    ap.add_argument("--old-partition", required=True,
                    help="partition.json from the OLD partitioner")
    ap.add_argument("--new-partition", required=True,
                    help="partition.json from the NEW partitioner")
    ap.add_argument("--out-dir", required=True,
                    help="directory for the 6 prompt files")
    a = ap.parse_args(argv)
    with open(a.gather, encoding="utf-8") as fh:
        gather = json.load(fh)
    with open(a.old_partition, encoding="utf-8") as fh:
        old_partition = json.load(fh)
    with open(a.new_partition, encoding="utf-8") as fh:
        new_partition = json.load(fh)
    os.makedirs(a.out_dir, exist_ok=True)
    prompts = build_prompts(gather, old_partition, new_partition)
    for name in sorted(prompts):
        with open(os.path.join(a.out_dir, name), "w",
                  encoding="utf-8") as fh:
            fh.write(prompts[name])
        print("wrote %s (%d bytes)"
              % (os.path.join(a.out_dir, name), len(prompts[name].encode(
                  "utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
