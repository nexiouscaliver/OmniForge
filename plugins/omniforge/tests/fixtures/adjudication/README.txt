Fixtures for omni_adjudicate (R2-B). Inputs are hand-authored test data.
expected_*.json are GENERATED — regenerate with the exact commands below, run with
cwd = this plugin's root directory (the worktree's plugins/omniforge/); outputs are machine-independent
(basename-only paths, no floats, fixed key order, indent=2, trailing newline).

# expected_clusters.json — the shipped consolidator over the fixture inputs:
python3 skills/omnireview-gitlab/scripts/omni_consolidate.py \
  --findings tests/fixtures/adjudication/codebase.findings.json \
             tests/fixtures/adjudication/security.findings.json \
             tests/fixtures/adjudication/analyst.findings.json \
  --prior tests/fixtures/adjudication/prior.json \
  --out-dir /tmp/r2b-golden
cp /tmp/r2b-golden/clusters.json tests/fixtures/adjudication/expected_clusters.json

# expected_adjudication_worklist.json — the shipped adjudicator over the golden clusters:
python3 skills/omnireview-gitlab/scripts/omni_adjudicate.py \
  --clusters /tmp/r2b-golden/clusters.json \
  --findings tests/fixtures/adjudication/codebase.findings.json \
             tests/fixtures/adjudication/security.findings.json \
             tests/fixtures/adjudication/analyst.findings.json \
  --out /tmp/r2b-golden/adjudication_worklist.json
cp /tmp/r2b-golden/adjudication_worklist.json \
   tests/fixtures/adjudication/expected_adjudication_worklist.json

# expected_post_payloads.json — the deterministic stand-in agent (T3), regen mode:
OMNIFORGE_REGEN_GOLDENS=1 python3 tests/test_omni_adjudicate_e2e.py TestE2E.test_payloads_golden
