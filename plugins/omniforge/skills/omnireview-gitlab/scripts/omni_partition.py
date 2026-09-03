#!/usr/bin/env python3
"""omni_partition.py — deterministic diff-based load balancing for the 3 reviewer
agents. Security-affinity files are owned by the security agent FIRST regardless
of size (security never receives greedy overflow); docs/config files prefer the
analyst (analyst wins exact load ties); every remaining file goes greedy
largest-first (ties: path asc) into the least-loaded of {codebase, analyst} only
(codebase wins exact ties). Every changed file gets exactly ONE owner; the
partition is a pure function of the input JSON — same input, byte-identical
output. Every agent still SEES every changed file (cross_cutting_files) — only
the deep-dive read depth is partitioned. Stdlib only. One JSON line on stdout;
diagnostics to stderr."""

import argparse
import json
import os
import re
import sys

AGENTS = ("analyst", "codebase", "security")

SECURITY_TOKENS = ("auth", "login", "session", "token", "secret", "password",
                   "crypto", "jwt", "permission")
SECURITY_PATHS = (".gitlab-ci.yml", ".github/workflows/", "dockerfile",
                  "docker-compose", ".env")
SECURITY_SUFFIXES = (".sql",)          # migrations/SQL
DOCS_SUFFIXES = (".md", ".txt", ".license")
DOCS_NAMES = ("license", "notice")


def is_security(path):
    p = path.lower()
    # tokens match path SEGMENTS, never substrings: "auth" must not fire on
    # author/AUTHORS, "token" not on tokenizer (underscore-compound names
    # like auth_middleware.py still match via the segment split)
    segments = set(re.split(r"[/_.-]", p))
    return (bool(segments & set(SECURITY_TOKENS))
            or any(s in p for s in SECURITY_PATHS)
            or p.endswith(SECURITY_SUFFIXES))


def is_docs(path):
    p = path.lower()
    return p.endswith(DOCS_SUFFIXES) or os.path.basename(p) in DOCS_NAMES \
        or (p.endswith(".json") and "config" in p)


def added_lines(mr, path):
    entry = (mr.get("diff_line_map") or {}).get(path)
    if isinstance(entry, dict):
        return len(entry.get("added_lines") or [])
    if isinstance(entry, int):
        return entry
    return 0


def partition(mr):
    """Pure function: saved fetch_mr_data JSON (dict) -> partition dict.

    Ownership passes, in order: (1) security affinity, (2) docs/config prefer
    analyst, (3) greedy largest-first over the remainder. Each pass iterates
    largest-first by added lines with path-ascending ties, and the emitted
    file lists use that same canonical order — so byte-identical output never
    depends on the input list order.
    """
    files_changed = [f for f in (mr.get("files_changed") or [])
                     if isinstance(f, str)]
    owned = {}                      # path -> (owner, reason)
    loads = {a: 0 for a in AGENTS}

    def sort_key(path):
        return (-added_lines(mr, path), path)

    # (1) security affinity FIRST — regardless of size; never greedy overflow
    for path in sorted((f for f in files_changed if is_security(f)),
                       key=sort_key):
        owned[path] = ("security", "security-affinity")
        loads["security"] += added_lines(mr, path)

    # (2) docs/config prefer analyst (analyst wins exact load ties)
    for path in sorted((f for f in files_changed
                        if f not in owned and is_docs(f)), key=sort_key):
        if loads["analyst"] <= loads["codebase"]:
            owned[path] = ("analyst", "docs-prefer-analyst")
            loads["analyst"] += added_lines(mr, path)
        else:
            owned[path] = ("codebase", "greedy-balance")
            loads["codebase"] += added_lines(mr, path)

    # (3) remaining files: greedy largest-first into the least-loaded of
    # {codebase, analyst} ONLY — codebase wins exact ties
    for path in sorted((f for f in files_changed if f not in owned),
                       key=sort_key):
        if loads["codebase"] <= loads["analyst"]:
            owned[path] = ("codebase", "greedy-balance")
            loads["codebase"] += added_lines(mr, path)
        else:
            owned[path] = ("analyst", "greedy-balance")
            loads["analyst"] += added_lines(mr, path)

    ordered = sorted(files_changed, key=sort_key)   # canonical emission order
    files_out = [{"path": p, "added_lines": added_lines(mr, p),
                  "owner": owned[p][0], "reason": owned[p][1]}
                 for p in ordered]
    agents_out = {a: {"files": [p for p in ordered if owned[p][0] == a],
                      "added_lines_total": loads[a],
                      "cross_cutting_files": list(ordered)}
                  for a in AGENTS}
    return {"files": files_out, "agents": agents_out}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mr-json", required=True,
                    help="saved Phase-1 fetch_mr_data JSON (files_changed + diff_line_map)")
    ap.add_argument("--out", required=True, help="partition.json output path")
    a = ap.parse_args(argv)
    try:
        with open(a.mr_json, "r", encoding="utf-8") as fh:
            mr = json.load(fh)
    except OSError as e:
        print(f"omni_partition: cannot read mr-json: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"omni_partition: mr-json is not valid JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(mr, dict):
        print("omni_partition: mr-json must be a JSON object", file=sys.stderr)
        return 2
    if isinstance(mr.get("data"), dict) and isinstance(mr.get("discussions"), (dict, list)):
        mr = mr["data"]          # gather-file form: operate on the embedded envelope
    result = partition(mr)
    try:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as e:
        print(f"omni_partition: cannot write out: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"files": len(result["files"]),
                      "agents": {ag: len(result["agents"][ag]["files"])
                                 for ag in AGENTS}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
