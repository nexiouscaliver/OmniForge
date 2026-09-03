#!/usr/bin/env python3
"""omni_fetch_mr.py — one-shot GitLab MR gather (W4 T1).

RED-commit skeleton stub: the full argparse surface only, so the contract
tests import cleanly. main() exits 2 with a stderr diagnostic and prints no
stdout JSON; the implementation lands in the GREEN commit.
"""

import argparse
import sys


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="One-shot GitLab MR gather (metadata, diff, discussions, "
                    "commits, versions, notes) into a single JSON file.")
    ap.add_argument("--project", required=True,
                    help="project ID or URL-encoded path")
    ap.add_argument("--mr", required=True, help="merge request IID")
    ap.add_argument("--out", default=None,
                    help="gather JSON output path (required in gather mode; "
                         "not used with --verify-head)")
    ap.add_argument("--host", default=None,
                    help="GitLab host (default: GITLAB_HOST env, "
                         "CI_API_V4_URL env, then https://gitlab.com)")
    ap.add_argument("--attempts", type=int, default=3,
                    help="per-call retry attempts (default 3)")
    ap.add_argument("--backoff-base", type=float, default=2.0,
                    help="retry backoff base in seconds (default 2.0)")
    ap.add_argument("--max-diff-lines", type=int, default=10000,
                    help="diff truncation line ceiling (default 10000)")
    ap.add_argument("--verify-head", default=None, metavar="RECORDED_SHA",
                    help="verify mode: ONE metadata GET comparing the MR's "
                         "current head SHA against the recorded one; "
                         "writes nothing")
    return ap.parse_args(argv)


def main(argv=None):
    parse_args(argv)
    print("omni_fetch_mr: not implemented", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
