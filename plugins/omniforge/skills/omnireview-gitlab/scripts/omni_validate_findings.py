#!/usr/bin/env python3
"""omni_validate_findings.py — validate the machine-readable findings block at the end
of a reviewer report. NEVER blocks a run: malformed input = pass-through, exit 0.
Stdlib only. Stdout: exactly one JSON line. Diagnostics: stderr."""

import argparse
import json
import os
import re
import sys

SEVERITIES = ("critical", "important", "minor")
AGENTS = ("analyst", "codebase", "security")


def find_block(text):
    """Last fenced ```json block from EOF backwards that parses as an array or an
    object with a "findings" key. Returns the parsed container or None."""
    for m in reversed(list(re.finditer(r"```json\s*\n(.*?)```", text, re.DOTALL))):
        try:
            obj = json.loads(m.group(1))
        except Exception:
            continue
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict) and isinstance(obj.get("findings"), list):
            return obj["findings"]
    return None


def check_finding(f, idx):
    """Return (finding_or_None, anomaly_or_None). Never clamp, never guess."""
    if not isinstance(f, dict):
        return None, f"finding {idx}: not an object — dropped"
    c = f.get("confidence")
    if not isinstance(c, int) or isinstance(c, bool) or not (0 <= c <= 100):
        return None, f"finding {idx}: confidence {c!r} out of range — dropped"
    if f.get("severity") not in SEVERITIES:
        return None, f"finding {idx}: severity {f.get('severity')!r} not in enum — dropped"
    lr = f.get("line_range", None)
    if lr is not None and (not isinstance(lr, list) or len(lr) != 2
                           or not all(isinstance(x, int) and not isinstance(x, bool) for x in lr)):
        return None, f"finding {idx}: line_range {lr!r} is not a 2-int array or null — dropped"
    for k in ("one_liner", "evidence"):
        if not isinstance(f.get(k), str) or not f.get(k).strip():
            return None, f"finding {idx}: {k} empty — dropped"
    return dict(f), None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--agent", required=True, choices=AGENTS)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    try:
        with open(a.report, "r", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        print(f"omni_validate_findings: cannot read report: {e}", file=sys.stderr)
        return 2
    anomalies, findings = [], []
    block = find_block(text)
    if block is None:
        anomalies.append("no parsable ```json findings block — pass-through")
    else:
        for i, f in enumerate(block, 1):
            if isinstance(f, dict) and isinstance(f.get("agent"), str) \
               and f.get("agent") not in (None, a.agent):
                anomalies.append(f"finding {i}: agent {f['agent']!r} != {a.agent!r} (stamped anyway)")
            ok, err = check_finding(f, i)
            if ok is None:
                anomalies.append(err)
            else:
                ok["agent"] = a.agent          # identity ALWAYS stamped from --agent
                findings.append(ok)
        if block and not findings:
            anomalies.append("zero valid findings — pass-through")
    passthrough = block is None or (bool(block) and not findings)
    out = {"agent": a.agent, "report": a.report, "findings": findings,
           "anomalies": anomalies, "passthrough": passthrough}
    out_path = a.out or (os.path.splitext(a.report)[0] + ".findings.json")
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as e:
        print(f"omni_validate_findings: cannot write findings file: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "validated": len(findings),
                      "anomalies": len(anomalies), "passthrough": passthrough}))
    for an in anomalies:
        print(f"omni_validate_findings: {an}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
