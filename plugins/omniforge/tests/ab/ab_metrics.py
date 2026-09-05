#!/usr/bin/env python3
"""ab_metrics.py — deterministic old-vs-new partition A/B metrics (R2-D
spec section 8.2, the SC-4 pass/fail authority; NO LLM anywhere).

Runs the OLD partitioner (fetched from a git base SHA via `git show`,
NEVER a checkout) and the NEW in-tree partitioner over the SAME three
corpora, evaluates both sides in the SAME currency, and writes the
committed ab_result.json artifact:

  (i)   real-or-synth — the frozen canary MR snapshot. Default = the
        committed ../fixtures/prepare/canary_gather.json (post-D7: a
        synthetic byte-copy of corpus (ii), so the run records the
        degeneracy instead of duplicating the check). The REAL !1402
        gather is engine-local only (spec D7): pass its path via
        --corpus-real to run corpus (i) on real data; the entry is then
        tagged source "local-uncommitted-real" and only these aggregate
        metrics (never content) are committed. (If the committed fixture
        itself is ever the real gather again — the D7 re-add-later case
        — the tag is "real-!1402"; see corpus_source.)
  (ii)  canary-synth  — the committed synthetic 110/109/9 shape control.
  (iii) security-light — the committed synthetic 80/40/2 corpus where
        old-vs-new shows the designed floor lift.

Old-side partitions lack weight fields, so per-file weights are
recomputed over the OLD ownership with the NEW weight function (pure,
hence exact) — both sides are compared in the same currency. Ratios are
integer permille of the ideal share (PD-2: no floats anywhere). Exit 0
iff overall PASS (the NEW side satisfies cap, floor, and the security
invariant on every unique corpus; the old side is reported as baseline
with no pass/fail authority). Retuning constants is R2-E/fleet scope,
never this artifact (D5/D6).
"""

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

AB = os.path.abspath(os.path.join(os.path.dirname(__file__)))
PARTITION_REL = ("skills", "omnireview-gitlab", "scripts",
                 "omni_partition.py")
DEFAULT_CORPUS_REAL = os.path.abspath(
    os.path.join(AB, "..", "fixtures", "prepare", "canary_gather.json"))
# The git-repo-relative path of the partition script, for `git show`.
PARTITION_GIT_REL = "plugins/omniforge/" + "/".join(PARTITION_REL)

NOTES = [
    "old-side weights recomputed with the NEW weight function over OLD "
    "ownership (same currency)",
    "PASS = section 8.2 admissibility rules; old side reported as "
    "baseline, no pass/fail authority",
    "weight-space deltas are NOT real-time gains - real-cost validation "
    "is R2-E/fleet (D5/D6)",
]


def permille(load, total):
    """load as integer permille of the ideal share (total/3): 1000 == ideal.

    Explicit zero-guard: `int(round(...)) or 0` cannot catch
    ZeroDivisionError — total == 0 must branch BEFORE the division.
    """
    return 0 if total == 0 else int(round(3000 * load / total))


def cap_strict(load, total):
    """Strict cap: load <= 1.25x ideal, exact-integer cross-multiply."""
    return 12 * load <= 5 * total


def floor_ok(load, total):
    """Floor: load >= 0.5x ideal, exact-integer cross-multiply."""
    return 6 * load >= total


def evaluate(partition, weight_fn, is_security_fn):
    """One side's metrics. OLD-side partitions lack weight fields — weights
    are recomputed over the OLD ownership with the NEW weight function
    (pure, so exact): both sides compared in the same currency. Emits the
    documented section 4.2 admissibility rules: strict where admissible,
    else the +W_max / -W_max bound with the oversize files itemized
    (reported, never silently). NO floats (PD-2): ratios are integer
    permille of ideal.
    """
    files = partition["files"]
    wf = {f["path"]: weight_fn(f["path"], f["added_lines"]) for f in files}
    total = sum(wf.values())
    wmax = max(wf.values(), default=0)
    agents = {}
    for a, info in partition["agents"].items():
        wt = info.get("weight_total")
        if wt is None:
            wt = sum(wf[f["path"]] for f in files if f["owner"] == a)
        agents[a] = {"file_count": len(info["files"]),
                     "weight_total": wt,
                     "weight_permille_of_ideal": permille(wt, total),
                     "cap_strict": cap_strict(wt, total),
                     "floor_ok": floor_ok(wt, total)}
    strict_cap = all(v["cap_strict"] for v in agents.values())
    strict_floor = all(v["floor_ok"] for v in agents.values())
    cap_pass = strict_cap or all(
        12 * v["weight_total"] <= 5 * total + 12 * wmax
        for v in agents.values())
    floor_pass = strict_floor or all(
        6 * v["weight_total"] >= total - 6 * wmax
        for v in agents.values())
    sec_ok = all(f["owner"] == "security"
                 and f["reason"] == "security-affinity"
                 for f in files if is_security_fn(f["path"]))
    return {"total_weight": total, "w_max": wmax, "agents": agents,
            "security_invariant": sec_ok,
            "oversize_files": [p for p, w in wf.items()
                               if 12 * w > 5 * total] if not strict_cap else [],
            "cap_pass": cap_pass, "floor_pass": floor_pass,
            "pass": cap_pass and floor_pass and sec_ok}


def _fail(msg):
    print("ab_metrics: %s" % msg, file=sys.stderr)
    sys.exit(1)


def fetch_base_script(plugin_root, base):
    """Materialize the BASE partitioner via `git show` into a temp file.

    NEVER a checkout. Exits 1 with a diagnostic naming the missing SHA if
    the show fails or returns empty (shallow-clone guard, plan risk 3) —
    never falls back to a different base.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", plugin_root, "show", "%s:%s" % (base,
                                                          PARTITION_GIT_REL)],
            capture_output=True, text=True, errors="replace", timeout=60)
    except subprocess.TimeoutExpired:
        _fail("git show for base %s timed out (60 s)" % base)
    if proc.returncode != 0 or not proc.stdout.strip():
        _fail("cannot fetch base partition script for %s:%s — is the SHA "
              "present in this repository's history? %s"
              % (base, PARTITION_GIT_REL, proc.stderr.strip()))
    tmpdir = tempfile.mkdtemp(prefix="ab_metrics_base_")
    path = os.path.join(tmpdir, "omni_partition_base.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(proc.stdout)
    return path


def run_partition(script_path, gather_path, out_path):
    """Run one partitioner script over one gather; return its partition.

    timeout parity with omni_prepare.run_partition (PARTITION_TIMEOUT_S).
    """
    try:
        proc = subprocess.run(
            [sys.executable, script_path, "--mr-json", gather_path,
             "--out", out_path],
            capture_output=True, text=True, errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        _fail("partitioner %s timed out (120 s) on %s"
              % (script_path, gather_path))
    if proc.returncode != 0:
        _fail("partitioner %s failed on %s (rc=%d): %s"
              % (script_path, gather_path, proc.returncode,
                 proc.stderr.strip()))
    with open(out_path, encoding="utf-8") as fh:
        return json.load(fh)


def corpus_source(gather_path, default_path):
    """Fact-derived provenance tag for a NON-degenerate corpus (i) (the
    degeneracy record short-circuits before this is consulted): a path
    other than the committed fixture was passed explicitly — engine-local
    real data (post-D7); the committed fixture itself, when its bytes
    differ from the synthetic copy (the D7 re-add-real-later case), is
    the committed real !1402 gather."""
    if os.path.abspath(gather_path) != default_path:
        return "local-uncommitted-real"
    return "real-!1402"


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _load_new_module(plugin_root):
    spec = importlib.util.spec_from_file_location(
        "omni_partition_ab_new",
        os.path.join(plugin_root, *PARTITION_REL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic old-vs-new partition A/B metrics "
                    "(spec section 8.2) — writes the committed "
                    "ab_result.json; exit 0 iff overall PASS")
    ap.add_argument("--base", default="501bba3",
                    help="git SHA holding the OLD partitioner "
                         "(default: 501bba3)")
    ap.add_argument("--out", required=True,
                    help="ab_result.json output path")
    ap.add_argument("--corpus-real", default=DEFAULT_CORPUS_REAL,
                    help="corpus (i) gather path (default: the committed "
                         "fixture — post-D7 a synthetic copy of corpus "
                         "(ii); pass the engine-local real gather path "
                         "to run corpus (i) on real data)")
    a = ap.parse_args(argv)

    plugin_root = os.path.abspath(os.path.join(AB, "..", ".."))
    new_script = os.path.join(plugin_root, *PARTITION_REL)
    mod = _load_new_module(plugin_root)
    synth_path = os.path.join(AB, "canary_synth_gather.json")
    corpora = [("real-or-synth", a.corpus_real),
               ("canary-synth", synth_path),
               ("security-light", os.path.join(AB,
                                               "security_light_gather.json"))]
    synth_sha = _sha256(synth_path)
    workdir = tempfile.mkdtemp(prefix="ab_metrics_run_")
    base_tmp = None
    try:
        old_script = fetch_base_script(plugin_root, a.base)
        base_tmp = os.path.dirname(old_script)
        out_corpora = {}
        overall_pass = True
        for name, gather_path in corpora:
            sha = _sha256(gather_path)
            if name == "real-or-synth" and sha == synth_sha:
                # degeneracy record (spec section 8.2): the committed
                # corpus-(i) fixture collapsed into corpus (ii) — record
                # it, do not duplicate the check
                out_corpora[name] = {
                    "source": "synthetic-fallback", "sha256": sha,
                    "degenerate_with": "canary-synth",
                    "note": "corpus (i) is byte-identical to corpus (ii) "
                            "- synthetic fallback collapse, recorded not "
                            "duplicated"}
                print("corpus %s: DEGENERATE with canary-synth "
                      "(synthetic fallback collapse, recorded)" % name)
                continue
            old_part = run_partition(old_script, gather_path,
                                     os.path.join(workdir,
                                                  "%s_old.json" % name))
            new_part = run_partition(new_script, gather_path,
                                     os.path.join(workdir,
                                                  "%s_new.json" % name))
            old_ev = evaluate(old_part, mod.weight, mod.is_security)
            new_ev = evaluate(new_part, mod.weight, mod.is_security)
            if name == "real-or-synth":
                source = corpus_source(gather_path, DEFAULT_CORPUS_REAL)
            else:
                source = name          # canary-synth / security-light
            out_corpora[name] = {"source": source, "sha256": sha,
                                 "old": old_ev, "new": new_ev,
                                 "new_pass": new_ev["pass"],
                                 "oversize_files":
                                     new_ev["oversize_files"]}
            if not new_ev["pass"]:
                overall_pass = False
            permilles = ", ".join(
                "%s %dpermille/%df" % (ag, v["weight_permille_of_ideal"],
                                       v["file_count"])
                for ag, v in sorted(new_ev["agents"].items()))
            print("corpus %s (%s): NEW [%s] pass=%s | OLD [%s]"
                  % (name, source, permilles, new_ev["pass"],
                     ", ".join("%s %dpermille/%df"
                               % (ag, v["weight_permille_of_ideal"],
                                  v["file_count"])
                               for ag, v in sorted(old_ev["agents"]
                                                   .items()))))
        doc = {"schema": "omniforge-ab-partition/1", "base": a.base,
               "weight_constants": {
                   "W_LANG_DEFAULT": mod.W_LANG_DEFAULT,
                   "W_LANG_SECURITY": mod.W_LANG_SECURITY,
                   "W_TEST_DEFAULT": mod.W_TEST_DEFAULT,
                   "W_TEST_FILE": mod.W_TEST_FILE},
               "corpora": out_corpora,
               "overall_pass": overall_pass,
               "notes": NOTES}
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print("overall_pass=%s -> %s" % (overall_pass, a.out))
        return 0 if overall_pass else 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if base_tmp:
            shutil.rmtree(base_tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
