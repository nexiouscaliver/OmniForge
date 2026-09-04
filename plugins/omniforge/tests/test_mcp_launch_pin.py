"""Contract test: the headless MCP launch line pins the mcp package range.

T0 evidence (w4-mcp-diagnosis.md, 3.3.1): the unpinned `.mcp.json` launch arg
`uv run --with mcp[cli]` resolves the latest mcp (2.x), where
`from mcp.server.fastmcp import FastMCP` dies at import and every tool call
returns -32000. The proven fix is pinning the 1.x range. This test pins the
pin: the arg after `--with` must be exactly `mcp[cli]>=1.0.0,<2.0.0` — never
the bare `mcp[cli]` spec that reintroduces the 2.x resolution.

Stdlib only — no pytest import — so the module runs identically under bare
unittest and pytest.
"""

import json
import os
import unittest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MCP_JSON = os.path.join(PLUGIN_DIR, ".mcp.json")

PINNED_SPEC = "mcp[cli]>=1.0.0,<2.0.0"


class McpLaunchPinTests(unittest.TestCase):
    def test_mcp_launch_pins_mcp_range(self):
        with open(MCP_JSON, encoding="utf-8") as fh:
            cfg = json.load(fh)
        args = cfg["omniforge"]["args"]
        self.assertIn("--with", args)
        spec = args[args.index("--with") + 1]
        self.assertEqual(spec, PINNED_SPEC,
                         "uv run --with must pin the mcp 1.x range "
                         "(unpinned resolves mcp 2.x, where "
                         "mcp.server.fastmcp is gone -> -32000)")
        self.assertNotIn("mcp[cli]", args,
                         "the bare unpinned mcp[cli] spec must not appear "
                         "as a launch arg")


if __name__ == "__main__":
    unittest.main()
