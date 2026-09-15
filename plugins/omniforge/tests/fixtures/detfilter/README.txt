Fixtures for omni_det_scan.py (det-scan v1 evidence packets; goal
det-filter-plugin-evidence, T1 packets + T2 diffs/packet variants). Inputs are
hand-authored test data — regenerate with the exact commands below, run with
cwd = this plugin's root directory (the worktree's plugins/omniforge/).

detfilter_packet_ok.json is the canonical valid packet (project 73279395, MR
!136, head bbb222 / base aaa111, gitleaks + opengrep both ok, one critical
secret finding at src/app.py:42 and one medium vuln finding at src/util.py:7,
uncapped). Every other packet fixture is DERIVED from it by the commands below
so the "ok + exactly one deviation" relation cannot drift.

# detfilter_packet_unknown_top.json — ok + one extra TOP-LEVEL key:
python3 - <<'EOF'
import json
D = "tests/fixtures/detfilter"
p = json.load(open(D + "/detfilter_packet_ok.json", encoding="utf-8"))
p["extra"] = 1
json.dump(p, open(D + "/detfilter_packet_unknown_top.json", "w", encoding="utf-8"), indent=2)
EOF

# detfilter_packet_bad_nested.json — ok but findings[1].severity outside the enum:
python3 - <<'EOF'
import json
D = "tests/fixtures/detfilter"
p = json.load(open(D + "/detfilter_packet_ok.json", encoding="utf-8"))
p["findings"][1]["severity"] = "catastrophic"
json.dump(p, open(D + "/detfilter_packet_bad_nested.json", "w", encoding="utf-8"), indent=2)
EOF

# detfilter_packet_additive.json — ok + unknown NESTED keys (tolerated everywhere):
python3 - <<'EOF'
import json
D = "tests/fixtures/detfilter"
p = json.load(open(D + "/detfilter_packet_ok.json", encoding="utf-8"))
p["mr"]["note"] = "engine detail"
p["scan"]["extra"] = {"anything": True}
p["findings"][0]["confidence"] = 82
p["meta"]["tag"] = "round-1"
json.dump(p, open(D + "/detfilter_packet_additive.json", "w", encoding="utf-8"), indent=2)
EOF

# detfilter_packet_other_mr.json — ok but iid/project_id of a DIFFERENT MR:
python3 - <<'EOF'
import json
D = "tests/fixtures/detfilter"
p = json.load(open(D + "/detfilter_packet_ok.json", encoding="utf-8"))
p["mr"]["iid"] = 999
p["mr"]["project_id"] = 888
json.dump(p, open(D + "/detfilter_packet_other_mr.json", "w", encoding="utf-8"), indent=2)
EOF

# detfilter_packet_not_json.txt — literal invalid JSON text:
python3 -c 'open("tests/fixtures/detfilter/detfilter_packet_not_json.txt", "w", encoding="utf-8").write("{not json")'

# detfilter_packet_unreadable.bin — invalid UTF-8 bytes (UnicodeDecodeError is a
# ValueError, so load_packet reports packet-not-json; the packet-unreadable
# reason is driven in tests by passing a DIRECTORY path, which raises
# IsADirectoryError portably — chmod-000 is not root-safe):
python3 -c 'open("tests/fixtures/detfilter/detfilter_packet_unreadable.bin", "wb").write(b"\xff\xfe{not utf8")'

T2 fixtures — the diffs pages carry the REAL array-of-items /diffs API shape
(headerless per-file hunks, exactly what assemble_diff synthesizes `+++ b/`
headers for; NEVER diff text). detfilter_mr_diffs_page1.json is EXACTLY 100
items because omni_glab_api.get_all turns the page only at exactly 100: 99
fillers f001..f099 + src/app.py whose hunk is ARITHMETIC-VERIFIED —
`@@ -38,4 +39,5 @@` with new_start=39: ctx->39, ctx->40, ctx->41, +leak->42,
ctx->43, so parse_diff_line_map yields added_lines == [42] (an earlier
`@@ -39,3 +39,4 @@` sketch parsed to [40] — do NOT use it). Page 2 serves
src/util.py (@@ -7,0 +7,1 @@ -> [7]) and src/extra.py (new file, line 1).

# detfilter_mr_diffs_page1.json + detfilter_mr_diffs_page2.json:
python3 - <<'EOF'
import json
D = "tests/fixtures/detfilter"
page1 = []
for i in range(1, 100):
    p = "src/f%03d.py" % i
    page1.append({"diff": "@@ -1,0 +1,1 @@\n+added\n", "old_path": p,
                  "new_path": p, "new_file": False, "deleted_file": False})
page1.append({
    "diff": "@@ -38,4 +39,5 @@\n ctx\n ctx\n ctx\n+leak = os.environ[\"KEY\"]\n ctx\n",
    "old_path": "src/app.py", "new_path": "src/app.py",
    "new_file": False, "deleted_file": False})
assert len(page1) == 100
json.dump(page1, open(D + "/detfilter_mr_diffs_page1.json", "w", encoding="utf-8"), indent=2)
page2 = [
    {"diff": "@@ -7,0 +7,1 @@\n+query\n", "old_path": "src/util.py",
     "new_path": "src/util.py", "new_file": False, "deleted_file": False},
    {"diff": "@@ -0,0 +1,1 @@\n+added\n", "old_path": "src/extra.py",
     "new_path": "src/extra.py", "new_file": True, "deleted_file": False},
]
json.dump(page2, open(D + "/detfilter_mr_diffs_page2.json", "w", encoding="utf-8"), indent=2)
EOF

# detfilter_packet_unanchored.json — ok but 4 findings: the 2 anchorable
# (src/app.py:42, src/util.py:7) INTERLEAVED with 1 wrong file (src/ghost.py:3)
# and 1 wrong line (src/app.py:999), so split order preservation is exercised:
python3 - <<'EOF'
import json
D = "tests/fixtures/detfilter"
ok = json.load(open(D + "/detfilter_packet_ok.json", encoding="utf-8"))
p = json.loads(json.dumps(ok))
p["findings"] = [
    ok["findings"][0],
    {"tool": "opengrep", "rule_id": "hardcoded-url", "file": "src/ghost.py",
     "line": 3, "severity": "low", "class": "other",
     "preview_redacted": "fetch(\"http://REDACTED:host:1a2b3c4d/api\")"},
    ok["findings"][1],
    {"tool": "trivy", "rule_id": "CVE-2026-1234", "file": "src/app.py",
     "line": 999, "severity": "high", "class": "sca",
     "preview_redacted": "libfoo 1.2.3 < 1.2.4 (fix: REDACTED:sca:99aa88bb)"},
]
json.dump(p, open(D + "/detfilter_packet_unanchored.json", "w", encoding="utf-8"), indent=2)
EOF

# detfilter_packet_head_mismatch.json — ok but head_sha of a DIFFERENT head:
python3 - <<'EOF'
import json
D = "tests/fixtures/detfilter"
p = json.load(open(D + "/detfilter_packet_ok.json", encoding="utf-8"))
p["mr"]["head_sha"] = "deadbee"
json.dump(p, open(D + "/detfilter_packet_head_mismatch.json", "w", encoding="utf-8"), indent=2)
EOF

# detfilter_packet_skipped.json — benign no-op scan (exit 0, nothing posted):
python3 - <<'EOF'
import json
D = "tests/fixtures/detfilter"
p = json.load(open(D + "/detfilter_packet_ok.json", encoding="utf-8"))
p["scan"]["status"] = "skipped"
p["scan"]["reason"] = "no pipeline token"
p["findings"] = []
json.dump(p, open(D + "/detfilter_packet_skipped.json", "w", encoding="utf-8"), indent=2)
EOF

# detfilter_packet_incomplete.json — posts WITH disclosure: timed-out trivy,
# capped meta with overflow, 1 anchorable finding:
python3 - <<'EOF'
import json
D = "tests/fixtures/detfilter"
p = json.load(open(D + "/detfilter_packet_ok.json", encoding="utf-8"))
p["scan"]["status"] = "incomplete"
p["scan"]["reason"] = "trivy timed out"
p["scan"]["tools"].append({"name": "trivy", "version": "0.55.0",
                           "duration_s": 600.0, "status": "timeout"})
p["findings"] = [p["findings"][0]]
p["meta"]["capped"] = True
p["meta"]["overflow_not_adjudicated"] = 3
json.dump(p, open(D + "/detfilter_packet_incomplete.json", "w", encoding="utf-8"), indent=2)
EOF
