A/B harness directory — corpora, metrics, and sim-prompt provenance
(R2-D spec sections 8.1-8.3; plan section 1.3).

(a) The corpora and their provenance

  canary_synth_gather.json  (corpus (ii), committed)
    SYNTHETIC 228-file canary shape (110 code-ish / 109 test-ish /
    9 security-ish; fixed seed 1402). The unconditionally-committed
    deterministic shape control — the corpus the weight-balance tests
    always run on. sha256
    ca94a1fa48a89f213f64377a47a7439dcc582ca46d3a0a3abe511c97780c96e5.

  security_light_gather.json  (corpus (iii), committed, REQUIRED)
    SYNTHETIC 80/40/2 shape (fixed seed 1403; the 2 security-ish files
    are <= 5 added lines). The deterministic shape where old-vs-new
    shows the designed floor lift: the OLD partitioner starves the
    third agent (2 tiny files, weight ~ 0), the NEW lifts it to
    >= 0.5x ideal.

  corpus (i) — "real-or-synth" — the frozen canary MR snapshot.
    CURRENT provenance of the committed default
    (../fixtures/prepare/canary_gather.json): a SYNTHETIC byte-copy of
    corpus (ii) (identical sha256) under Deviation D7 — the REAL !1402
    gather was fetched successfully 2026-09-05 (exit 0, 1551584 bytes,
    227 files at the frozen head, sha256
    23bf9b121816531535469e8d21d0123730bf495e213f35bae97c8c11ba868e7b),
    but its source project (GitLab id 73281071 — the numeric id already
    public in the generator stub; the project is otherwise unnamed
    here) is PRIVATE and this plugin repo is PUBLIC: committing the
    gather would publish a private project's source diff and the bot's
    security-review discussion threads. The real gather is therefore
    retained ENGINE-LOCAL ONLY (regenloop/local/r2d/
    canary_1402_real_gather.json in the engine repo, gitignored, never
    committed here). Corpus (i) metrics run on the REAL data via

      ab_metrics.py --corpus-real <engine-local real gather path>

    which tags the entry source "local-uncommitted-real" and commits
    ONLY the aggregate metrics (numbers/paths) in ab_result.json. When
    ab_metrics.py runs with the DEFAULT --corpus-real (the committed
    synthetic copy), corpus (i) is byte-identical to corpus (ii) and
    the result records the degeneracy explicitly ("degenerate_with":
    "canary-synth") instead of duplicating the check — the committed
    ab_result.json reflects the REAL (non-degenerate) run.

(b) Generator regeneration — the committed fixtures are GENERATED;
    regenerate with these exact commands (cwd = plugins/omniforge):

      python3 tests/ab/make_canary_synthetic.py --profile canary --out tests/ab/canary_synth_gather.json
      python3 tests/ab/make_canary_synthetic.py --profile security-light --out tests/ab/security_light_gather.json

    The generator self-checks shape counts + classifier agreement and
    exits 2 on drift; tests/ab/test_ab_harness.py byte-compares the
    committed fixtures against fresh generation (drift fails the suite).
    canary_synth_gather.json and security_light_gather.json are
    byte-stable: same profile -> identical bytes.
    ../fixtures/prepare/canary_gather.json is a byte-copy of
    canary_synth_gather.json (regenerate the copy the same way if ever
    needed; see that directory's README for its own provenance).

(c) ab_metrics.py — the deterministic SC-4 A/B evidence (spec 8.2).

    Committed-result invocation (cwd = plugins/omniforge; corpus (i) on
    the ENGINE-LOCAL real gather, see (a)):

      python3 tests/ab/ab_metrics.py --base 501bba3 \
        --corpus-real <engine-local real gather path> \
        --out tests/ab/ab_result.json

    Without --corpus-real the default committed fixture runs and the
    degeneracy record fires (see (a)). The OLD side is fetched via
    `git show <base>:plugins/omniforge/skills/omnireview-gitlab/scripts/
    omni_partition.py` into a temp file — never a checkout. Old-side
    weights are recomputed with the NEW weight function over the OLD
    ownership (same currency). PASS rule: exit 0 iff the NEW side
    satisfies cap, floor, and the security invariant on every UNIQUE
    corpus (strict where admissible, else the section 4.2 +/-W_max
    bound with oversize files itemized); the OLD side is reported as
    baseline with no pass/fail authority. ab_result.json is committed
    byte-as-generated.

(d) make_ab_prompts.py — reviewer-sim prompt slicer (spec 8.3).

    Invocation (cwd = plugins/omniforge; against the engine-local real
    gather, whose data.diff carries (truncated) diff text — the
    synthetic gathers have NO diff field):

      python3 tests/ab/make_ab_prompts.py --gather <gather.json> \
        --old-partition <old partition.json> \
        --new-partition <new partition.json> --out-dir <dir>

    Writes 6 deterministic files {old,new}_{analyst,codebase,security}.md
    (3-line header + the VERBATIM pinned sim instruction + per-owned-file
    fenced diff chunks in canonical partition order). The 6 Agent
    dispatches are ORCHESTRATOR-RUN, ILLUSTRATIVE-ONLY, with NO
    authority (D6): the uniform-cost sim is circular for validating a
    non-uniform cost model; single-pair wall differences inside the
    +/-75 s noise band (w3-latency-variance.md section 4) are noise and
    pooling is impractical locally; sim numbers carry no pass/fail or
    retune weight — the <=150 s REAL-skew target is R2-E's release
    gate. Never copy real diff text into committed files.
