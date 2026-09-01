#!/usr/bin/env python3
"""omni_wait.py — chunked subagent-completion waiter for omnireview-gitlab (v3.2.0).

Exit codes are the skill's SOLE completion authority: 0 all-terminal, 3 still-running
(re-invoke), 2 degraded (budget exhausted | every non-terminal stalled | count mismatch).
Stdout: exactly one JSON status line. Diagnostics go to stderr. Stdlib only."""

import argparse
import glob
import json
import os
import signal
import sys
import time

MARKER = "Status: DONE"
EXIT_OK, EXIT_DEGRADED, EXIT_RUNNING = 0, 2, 3
TEXT_CAP = 256 * 1024      # per-agent final_text cap (256 KiB)
TAIL_WINDOW = 256 * 1024   # initial tail-read window; doubles up to whole-file size
REPORT_PREFIX = "omni_wait-"   # waiter-owned report filename prefix (wipe-scope fence)


def _alarm_delay(chunk_timeout):
    # SIGALRM strictly inside the chunk budget: the margin is 5 % of the budget
    # clamped to [0.05, 15] s, subtracted, floored at 0.05 s — 480→465 s,
    # 10→9.5 s, 2→1.9 s; a budget ≤ 0.10 s floors the delay to 0.05 s.
    margin = max(0.05, min(15.0, chunk_timeout * 0.05))
    return max(0.05, chunk_timeout - margin)


def _platform_supports_alarm():
    """POSIX interval timers are required for a bounded wait chunk; platforms
    without them (Windows) get a fail-fast degraded exit instead of a crash."""
    return hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")


class _ChunkElapsed(Exception):
    """Raised by the SIGALRM handler when the chunk budget elapses."""


def _on_alarm(signum, frame):
    raise _ChunkElapsed


def parse_args(argv):
    ap = argparse.ArgumentParser(
        description="Chunked subagent-completion waiter (exit 0/3/2, one JSON line on stdout).")
    ap.add_argument("--transcript", action="append", default=[], metavar="P",
                    help="transcript path (repeatable; explicit paths win over --scan-dir)")
    ap.add_argument("--expect", type=int, required=True,
                    help="number of subagent transcripts expected")
    ap.add_argument("--chunk-timeout", type=float, default=480,
                    help="max seconds per invocation before exiting 3 (default 480)")
    ap.add_argument("--total-budget", type=float, default=1800,
                    help="overall wait budget since --since (default 1800)")
    ap.add_argument("--since", type=float, default=None,
                    help="dispatch epoch; defaults to this invocation's start")
    ap.add_argument("--scan-dir", default=None,
                    help="directory globbed for agent-*.jsonl when --transcript is absent")
    ap.add_argument("--stable-window", type=float, default=20,
                    help="mtime stability window for marker-less completion (default 20)")
    ap.add_argument("--stall-after", type=float, default=300,
                    help="mtime silence after which a non-terminal agent is stalled (default 300)")
    ap.add_argument("--poll-interval", type=float, default=2,
                    help="seconds between transcript re-evaluations (default 2)")
    ap.add_argument("--count-grace", type=float, default=15,
                    help="spawn grace for scan-dir under-counts before count_mismatch (default 15)")
    ap.add_argument("--reports-dir", default=None,
                    help="also write status.json + one markdown report per agent here")
    args = ap.parse_args(argv)
    # Post-parse validation (not argparse, to keep the JSON contract): this is a
    # usage error like argparse's own — stderr + exit 2, no JSON on stdout.
    if not args.transcript and not args.scan_dir:
        print("omni_wait: one of --transcript or --scan-dir is required", file=sys.stderr)
        ap.print_usage(sys.stderr)
        raise SystemExit(EXIT_DEGRADED)
    # Numeric-flag validation (copilot review r2): a non-positive duration breaks
    # the one-JSON-line contract outright (time.sleep raises on <= 0) or silently
    # corrupts semantics (stable-window <= 0 → everything instantly terminal;
    # expect < 1 → vacuous all-terminal). Same usage-error path as above.
    bad = []
    if args.expect < 1:
        bad.append("--expect must be >= 1")
    for name, value in (("--chunk-timeout", args.chunk_timeout),
                        ("--total-budget", args.total_budget),
                        ("--stable-window", args.stable_window),
                        ("--stall-after", args.stall_after),
                        ("--poll-interval", args.poll_interval)):
        if value <= 0:
            bad.append("%s must be > 0" % name)
    if args.count_grace < 0:
        bad.append("--count-grace must be >= 0")
    if bad:
        for msg in bad:
            print("omni_wait: invalid value: %s" % msg, file=sys.stderr)
        ap.print_usage(sys.stderr)
        raise SystemExit(EXIT_DEGRADED)
    return args


def resolve_transcripts(args):
    """Return (paths, from_scan). Explicit --transcript paths are used verbatim,
    existing or not. Scan-dir globs agent-*.jsonl (the agent-* pattern excludes
    the main session's <session-id>.jsonl), falling back to scan_dir/subagents/;
    with --since, only files with mtime >= since are kept."""
    if args.transcript:
        return list(args.transcript), False
    paths = sorted(glob.glob(os.path.join(args.scan_dir, "agent-*.jsonl")))
    if not paths:
        sub = os.path.join(args.scan_dir, "subagents")
        if os.path.isdir(sub):
            paths = sorted(glob.glob(os.path.join(sub, "agent-*.jsonl")))
    if args.since is not None:
        kept = []
        for p in paths:
            try:
                if os.path.getmtime(p) >= args.since:
                    kept.append(p)
            except OSError:
                continue
        paths = kept
    return paths, True


def _is_assistant(record):
    if not isinstance(record, dict):
        return False
    if record.get("type") == "assistant":
        return True
    message = record.get("message")
    return isinstance(message, dict) and message.get("role") == "assistant"


def _extract_text(record):
    """Harvest text from an assistant record REGARDLESS of tool_use blocks:
    the joined text blocks of a list content, or string content verbatim.
    Empty string when there is no text. This is the scan-back harvest —
    independent of classify_record's terminality kind, so a mixed
    text+tool_use record still CONTRIBUTES final_text while classify
    (correctly) refuses to call it terminal."""
    message = record.get("message") if isinstance(record, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text") or "" for block in content
                       if isinstance(block, dict) and block.get("type") == "text")
    return ""


def classify_record(record):
    """Return ("final_text", text) / ("tool_use", None) / ("other", None).
    message.content may be a list of blocks or a plain string. A record with
    ANY tool_use block is ("tool_use", None) even when it also carries text —
    that mixed shape is a RUNNING agent mid-tool-call, never terminal. Any
    unrecognized shape returns ("other", None) — callers treat that as
    running, never terminal."""
    if not isinstance(record, dict):
        return ("other", None)
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return ("final_text", content)
    if isinstance(content, list):
        has_tool_use = any(isinstance(b, dict) and b.get("type") == "tool_use"
                           for b in content)
        if has_tool_use:
            return ("tool_use", None)
        text = _extract_text(record)
        if text:
            return ("final_text", text)
    return ("other", None)


def final_text_of(lines):
    """Scan lines REVERSED (tail-first) and return (last_assistant_record,
    final_text): the last complete assistant record (the completion predicate —
    trailing non-assistant records such as user / tool_result / system / summary
    are skipped) and the text of the last assistant record CONTAINING text
    (harvested via _extract_text, so scanning back past tool_use-bearing
    assistant records — mixed or not — an agent that died mid-tool-call still
    yields its findings-so-far). Torn/partial trailing lines fail json.loads
    and are skipped by construction."""
    record = None
    text = None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not _is_assistant(rec):
            continue
        if record is None:
            record = rec
        if text is None:
            harvested = _extract_text(rec)
            if harvested:
                text = harvested
        if record is not None and text is not None:
            break
    return record, text


# path -> {"size", "window", "record", "text"} from the last successful scan:
# an unchanged file (same size) skips the rescan entirely, and a grown file
# resumes at the already-paid-for window instead of re-running the doubling
# ladder from TAIL_WINDOW on every poll.
_SCAN_MEMO = {}


def read_last_assistant_record(path):
    """Read the transcript tail-first (256 KiB window, doubling up to whole-file
    size) and return (last_assistant_record, final_text). (None, None) when the
    file is unreadable, empty, or contains no assistant record."""
    try:
        size = os.path.getsize(path)
    except OSError:
        _SCAN_MEMO.pop(path, None)
        return None, None
    memo = _SCAN_MEMO.get(path)
    if memo is not None and size == memo["size"]:
        return memo["record"], memo["text"]   # unchanged file: same answer, no rescan
    window = TAIL_WINDOW
    if memo is not None and size > memo["size"]:
        # Append-only growth: the old window plus the new bytes still covers
        # everything the last scan reached — don't re-climb the doubling ladder.
        window = memo["window"] + (size - memo["size"])
    while True:
        try:
            with open(path, "rb") as f:
                f.seek(max(0, size - window))
                data = f.read()
        except OSError:
            _SCAN_MEMO.pop(path, None)
            return None, None
        record, text = final_text_of(data.decode("utf-8", errors="replace").splitlines())
        if (record is not None and text is not None) or window >= size:
            _SCAN_MEMO[path] = {"size": size, "window": window,
                                "record": record, "text": text}
            return record, text
        window *= 2


def evaluate(path, now, stable_window, stall_after, since_fallback):
    """Per-transcript entry with the exact contract keys."""
    if not os.path.exists(path):
        age = now - since_fallback          # age from --since (or invocation start) when missing
        stalled = age > stall_after
        return {
            "path": path,
            "state": "stalled" if stalled else "missing",
            "terminal_via": None,
            "mtime_age_s": round(age, 3),
            "harvested_partial": stalled,
            "final_text": None,
            "final_text_truncated": False,
        }
    try:
        mtime_age = now - os.stat(path).st_mtime
    except OSError:
        mtime_age = 0.0
    record, harvested = read_last_assistant_record(path)
    kind, text = classify_record(record)    # record None -> ("other", None) -> running
    state, via = "running", None
    if kind == "final_text" and MARKER in text:
        state, via = "terminal", "marker"   # the marker never blocks on mtime
    elif kind == "final_text" and mtime_age >= stable_window:
        state, via = "terminal", "mtime"
    elif mtime_age > stall_after:
        state = "stalled"                   # stall override for candidate-running entries
    entry = {
        "path": path,
        "state": state,
        "terminal_via": via,
        "mtime_age_s": round(mtime_age, 3),
        "harvested_partial": state == "stalled",
        "final_text": None,
        "final_text_truncated": False,
    }
    if harvested is not None:
        if len(harvested) > TEXT_CAP:
            entry["final_text"] = harvested[:TEXT_CAP]
            entry["final_text_truncated"] = True
        else:
            entry["final_text"] = harvested
    return entry


def _write_reports(reports_dir, status):
    """Write the status JSON (pretty-printed — a single line with inline
    final_text would face the Read tool's long-line truncation) plus one
    markdown report per agent, named omni_wait-<transcript-basename>.md.
    Wipes ONLY waiter-owned names from prior invocations — omni_wait-*.md and
    status.json — never arbitrary *.md files the caller may keep in the dir
    (a leftover waiter report is a contamination hazard for a consumer that
    globs; an unrelated file is not ours to delete). Idempotent: deterministic
    filenames, plain overwrite."""
    os.makedirs(reports_dir, exist_ok=True)
    for name in os.listdir(reports_dir):
        if (name.startswith(REPORT_PREFIX) and name.endswith(".md")) \
                or name == "status.json":
            try:
                os.remove(os.path.join(reports_dir, name))
            except OSError:
                pass
    with open(os.path.join(reports_dir, "status.json"), "w") as f:
        json.dump(status, f, indent=2)
    for entry in status["transcripts"]:
        base = REPORT_PREFIX + os.path.basename(entry["path"]) + ".md"
        text = entry["final_text"]
        if text is None:
            text = "no report harvested (state: %s)\n" % entry["state"]
        elif not text.endswith("\n"):
            text += "\n"
        with open(os.path.join(reports_dir, base), "w") as f:
            f.write(text)


def emit(args, entries, code, reason, since, start):
    """Build the one-line JSON status, optionally write the reports channel
    FIRST, then print the sole stdout line. Returns the exit code."""
    status = {
        "expect": args.expect,
        "exit_code": code,
        "exit_reason": reason,
        "ts": int(time.time()),
        "since": since,   # EFFECTIVE anchor (invocation start when --since omitted) —
                          # always numeric so deadline == int(since + total_budget) reconstructs
        "deadline": int(since + args.total_budget),
        "elapsed_s": round(time.time() - start, 3),
        "transcripts": entries,
    }
    if args.reports_dir:
        try:
            _write_reports(args.reports_dir, status)
        except OSError as exc:
            # The reports dir is an enhancement channel; it must never crash the
            # sole completion authority — degrade to stdout-only.
            print("omni_wait: reports-dir write failed (%s); continuing stdout-only" % exc,
                  file=sys.stderr)
    print(json.dumps(status), flush=True)
    return code


def main(argv):
    args = parse_args(argv)
    start = time.time()
    since = args.since if args.since is not None else start
    deadline = since + args.total_budget
    paths, from_scan = resolve_transcripts(args)

    # Count check FIRST, before any waiting.
    if len(paths) != args.expect:
        under = len(paths) < args.expect
        # Grace only with an explicit --since: without one, since defaults to
        # this invocation's start, the grace branch would always hold, and each
        # re-invoke would reset it — an unbounded count_pending loop.
        if (from_scan and under and args.since is not None
                and time.time() < since + args.count_grace):
            # Slow-spawning agents haven't written transcripts yet — keep waiting.
            entries = [evaluate(p, time.time(), args.stable_window, args.stall_after, since)
                       for p in paths]
            return emit(args, entries, EXIT_RUNNING, "count_pending", since, start)
        # Explicit paths get no grace; over-counts are loud too. The resolved
        # paths are STILL evaluated — refusing to wait must not throw away the
        # harvest the completed reviewers already produced.
        print("omni_wait: transcript count mismatch: found %d, expected %d — refusing to wait on a wrong count"
              % (len(paths), args.expect), file=sys.stderr)
        entries = [evaluate(p, time.time(), args.stable_window, args.stall_after, since)
                   for p in paths]
        return emit(args, entries, EXIT_DEGRADED, "count_mismatch", since, start)

    if not _platform_supports_alarm():
        # Fail fast (copilot review): a platform without POSIX timers cannot run
        # a bounded chunk — one clear stderr line + the one-line JSON contract,
        # degraded. Best-effort entries are still emitted so callers see state.
        print("omni_wait: SIGALRM/setitimer unavailable on this platform — a bounded "
              "wait chunk cannot run; degrading (sigalrm_unsupported)", file=sys.stderr)
        entries = [evaluate(p, time.time(), args.stable_window, args.stall_after, since)
                   for p in paths]
        return emit(args, entries, EXIT_DEGRADED, "sigalrm_unsupported", since, start)

    signal.signal(signal.SIGALRM, _on_alarm)
    entries = []
    armed = False
    try:
        while True:
            now = time.time()
            entries = [evaluate(p, now, args.stable_window, args.stall_after, since)
                       for p in paths]
            if all(e["state"] == "terminal" for e in entries):
                signal.setitimer(signal.ITIMER_REAL, 0)
                return emit(args, entries, EXIT_OK, "all_terminal", since, start)
            pending = [e for e in entries if e["state"] != "terminal"]
            if all(e["state"] == "stalled" for e in pending):
                signal.setitimer(signal.ITIMER_REAL, 0)
                return emit(args, entries, EXIT_DEGRADED, "stall", since, start)
            if time.time() >= deadline:
                signal.setitimer(signal.ITIMER_REAL, 0)
                return emit(args, entries, EXIT_DEGRADED, "budget_exhausted", since, start)
            if not armed:
                # Arm only AFTER a full evaluate (strictly inside the chunk
                # budget): the alarm path then always emits real entries, never
                # an empty transcript list.
                signal.setitimer(signal.ITIMER_REAL, _alarm_delay(args.chunk_timeout))
                armed = True
            time.sleep(min(args.poll_interval, max(0.05, deadline - time.time())))
    except _ChunkElapsed:
        # Disarm BEFORE building/emitting the JSON so a late alarm can never
        # tear the one-line stdout contract.
        signal.setitimer(signal.ITIMER_REAL, 0)
        return emit(args, entries, EXIT_RUNNING, "chunk_elapsed", since, start)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
