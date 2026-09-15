Fixtures for omni_det_scan.py (det-scan v1 evidence packets; goal
det-filter-plugin-evidence, T1). Inputs are hand-authored test data — regenerate
with the exact commands below, run with cwd = this plugin's root directory (the
worktree's plugins/omniforge/).

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
