Prepare fixtures — provenance and regeneration (R2-D plan section 4; D7).

canary_gather.json
  SYNTHETIC byte-copy of tests/ab/canary_synth_gather.json (228 files:
  110 code-ish / 109 test-ish / 9 security-ish; seed 1402), produced by
  tests/ab/make_canary_synthetic.py --profile canary. DISCLOSED
  substitution (spec section 8.1 fallback / OQ-3, and Deviation D7):
  the REAL !1402 gather was fetched 2026-09-05 via
  skills/omnireview-gitlab/scripts/omni_fetch_mr.py
  (--project 73281071 --mr 1402; exit 0; 1551584 bytes; 227 files;
  sha256 23bf9b121816531535469e8d21d0123730bf495e213f35bae97c8c11ba868e7b),
  but project 73281071 is PRIVATE and the gather embeds the project's
  source diff plus security-review discussion threads — committing it to
  this PUBLIC repo would publish private security material. Under D7 the
  real gather is retained ENGINE-LOCAL ONLY at
  regenloop/local/r2d/canary_1402_real_gather.json (gitignored, never
  pushed) and is NOT in this repo; it remains the corpus-(i) instrument
  for T3 via ab_metrics.py --corpus-real. The committed fixture is the
  deterministic synthetic shape control (identical to corpus (ii)).

pretrim_agent-{1,2,3}.md
  GENERATED (analyst/codebase/security rendered in that order) by the
  BASE — pre-trim — render_brief(), extracted from the base commit
  because the in-tree renderer is trimmed from the T2 green commit on:

    git show 501bba3:plugins/omniforge/skills/omnireview-gitlab/scripts/omni_prepare.py > /tmp/omni_prepare_base.py

  over the CURRENT omni_partition.partition (T1's cost-weighted
  partitioner; 76/75/77 files on the synthetic 228-file corpus). They
  are the measured pre-trim baseline for the -40% byte assertion.
  Regenerate (cwd = plugins/omniforge) AFTER the git show above:

    python3 - <<'PY'
    import importlib.util, json, os, sys
    SCRIPTS = os.path.abspath(os.path.join(
        "skills", "omnireview-gitlab", "scripts"))
    sys.path.insert(0, SCRIPTS)
    spec = importlib.util.spec_from_file_location(
        "omni_prepare_base", "/tmp/omni_prepare_base.py")
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    spec2 = importlib.util.spec_from_file_location(
        "omni_partition_cur", os.path.join(SCRIPTS, "omni_partition.py"))
    part_mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(part_mod)
    with open("tests/fixtures/prepare/canary_gather.json",
              encoding="utf-8") as fh:
        gather = json.load(fh)
    gather["review_id"] = "canary-1402"
    part = part_mod.partition(gather["data"])
    for agent, n in (("analyst", 1), ("codebase", 2), ("security", 3)):
        with open("tests/fixtures/prepare/pretrim_agent-%d.md" % n,
                  "w", encoding="utf-8") as fh:
            fh.write(prep.render_brief(agent, gather, part, None))
    PY

  (The synthetic gather itself regenerates with
  make_canary_synthetic.py — see tests/ab/.)

expected_agent-{1,2,3}.md
  HAND-AUTHORED contracts (the R2-D trimmed shape): today's bytes with
  the cross-cutting bullet list replaced by the single pointer line
  "Cross-cutting: all 4 changed files (see partition.json)" and the
  "- Cross-cutting: 4 files" Stats bullet removed. The renderer is built
  to match them — NEVER generate these from render_brief().

gather_input.json / expected_prepare.json
  Pre-existing round2-pre-dispatch fixtures; untouched by R2-D.
