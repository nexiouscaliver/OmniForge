<p align="center">
  <h1 align="center">OmniReview</h1>
  <p align="center">
    <strong>Three agents. Three perspectives. One report you can trust.</strong>
  </p>
  <p align="center">
    Multi-agent adversarial merge request review — powered by parallel AI agents in isolated environments.
  </p>
  <p align="center">
    <a href="#installation">Install</a> &middot;
    <a href="#how-it-works">How It Works</a> &middot;
    <a href="#what-happens-after-review">Post-Review Actions</a> &middot;
    <a href="#contributing">Contribute</a>
  </p>
</p>

---

## What is OmniReview?

Code reviews are one of the most important quality gates in software development. But they're also one of the most inconsistent — reviewers get tired, skip files, miss security implications, or rubber-stamp "small" changes that turn out to break production.

**OmniReview changes that.** It runs three independent AI review agents *simultaneously*, each in its own isolated copy of your codebase, each looking at your merge request from a completely different angle. When they're done, their findings are cross-referenced, scored for confidence, filtered for false positives, and delivered as a single consolidated report.

No more "LGTM" reviews. No more missed security holes. No more "this was too small to review properly."

### Currently Supported

| Platform | Tool | Status |
|----------|------|--------|
| **GitLab** merge requests | `glab` CLI | Supported |
| **Claude Code** (Anthropic) | Personal skills | Supported |

### Coming Soon

Support for additional platforms and AI coding tools is on the roadmap:

- **GitHub** pull requests via `gh` CLI
- **Cursor** IDE integration
- **Gemini CLI** (Google) agent compatibility
- **OpenCode** support
- **Kilo Code** integration
- **Other AI coding assistants** as the ecosystem evolves

---

## The Three Agents

OmniReview doesn't just run one review — it runs three, each with a distinct purpose and expertise. They work in parallel, in complete isolation from each other, so their findings are genuinely independent.

### MR Analyst (OmniReview)

*"Is this merge request well-crafted?"*

The MR Analyst focuses on **process quality** — the things that have nothing to do with the code itself but everything to do with whether the change is safe to merge:

- Examines every commit one by one — are messages clear? Is each commit atomic?
- Checks the MR description — does it explain *what* changed and *why*?
- Reviews all discussion threads — are reviewer concerns addressed or left hanging?
- Evaluates scope — is this MR focused, or is it sneaking in unrelated changes?
- Verifies CI/CD pipeline status

### Codebase Reviewer (OmniReview)

*"Is this code correct, clean, and well-integrated?"*

The Codebase Reviewer performs a **deep code review** that goes far beyond the diff. It has full access to your codebase and is instructed to trace call chains, check test coverage, and verify architectural consistency:

- Reads the complete files that were changed (not just the diff lines)
- Traces imports, callers, and dependencies to understand impact
- Checks for logic errors, edge cases, and race conditions
- Verifies test coverage — are new features actually tested?
- Evaluates architecture — does this change fit the existing codebase patterns?
- Flags performance concerns like N+1 queries or blocking async calls

### Security Reviewer (OmniReview)

*"Can this change be exploited?"*

The Security Reviewer thinks like an attacker. It systematically walks through the OWASP Top 10 checklist and looks for vulnerabilities both in the changes and in how they interact with existing code:

- **Injection** — SQL, XSS, command injection, path traversal
- **Broken access control** — missing authorization, privilege escalation
- **Cryptographic failures** — hardcoded secrets, weak algorithms
- **Authentication issues** — JWT validation, session handling
- **Data exposure** — PII leaks, overly broad API responses
- **SSRF, misconfigurations, vulnerable dependencies**, and more
- Scans for hardcoded secrets, API keys, and credentials in the diff and commit history

---

## How It Works

OmniReview follows a strict 7-phase process. Every merge request gets the full treatment — no shortcuts, no exceptions.

```
                         MR !123
                            |
                            v
              +--------------------------+
              |  Phase 1: GATHER         |
              |  Fetch MR metadata,      |
              |  diff, comments, commits |
              +--------------------------+
                            |
                            v
              +--------------------------+
              |  Phase 2: ISOLATE        |
              |  Create 3 git worktrees  |
              |  on the MR source branch |
              +--------------------------+
                            |
                            v
        +-------------------+-------------------+
        |                   |                   |
        v                   v                   v
+----------------+  +----------------+  +----------------+
|  MR Analyst    |  |  Codebase      |  |  Security      |
|  (OmniReview)  |  |  Reviewer      |  |  Reviewer      |
|                |  |  (OmniReview)  |  |  (OmniReview)  |
|  Commits,      |  |  Code quality, |  |  OWASP Top 10, |
|  discussions,  |  |  architecture, |  |  secrets,       |
|  MR hygiene    |  |  testing       |  |  auth/authz     |
+----------------+  +----------------+  +----------------+
        |                   |                   |
        v                   v                   v
              +--------------------------+
              |  Phase 4: CONSOLIDATE    |
              |  Confidence scoring,     |
              |  cross-correlation,      |
              |  deduplication           |
              +--------------------------+
                            |
                            v
              +--------------------------+
              |  Phase 5: REPORT         |
              |  Structured findings     |
              |  with verdict            |
              +--------------------------+
                            |
                            v
              +--------------------------+
              |  Phase 6: ACT            |
              |  You choose what to do   |
              +--------------------------+
                            |
                            v
              +--------------------------+
              |  Phase 7: CLEANUP        |
              |  Remove all worktrees    |
              |  (always runs)           |
              +--------------------------+
```

### Why Git Worktrees?

Each agent gets its own complete copy of your repository through [git worktrees](https://git-scm.com/docs/git-worktree). This means:

- Agents can freely navigate the codebase without interfering with each other
- Each agent sees the exact state of the MR's source branch
- Your working directory is never touched
- Everything is cleaned up automatically when the review finishes

### Built-in MCP Tool Server

OmniReview includes a Python [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that runs as a local child process alongside Claude Code. Instead of executing 25+ individual shell commands for worktree management and MR data fetching, the MCP server exposes 3 dedicated tools that Claude calls directly:

| Tool | What It Does | Replaces |
|------|-------------|----------|
| `fetch_mr_data` | Fetches MR metadata, comments, diff, commits, and file list in a single structured call | 6 separate `glab`/`git` commands |
| `create_review_worktrees` | Atomically creates 3 isolated worktrees with stale cleanup, gitignore management, and absolute path resolution | 12 manual `git worktree` commands |
| `cleanup_review_worktrees` | Force-removes all worktrees with fallback cleanup, guaranteed to leave no stale state | 7 cleanup commands |

The MCP server is security-hardened:
- All subprocess calls use `create_subprocess_exec` (argument list, no shell interpretation) to prevent injection
- Input validation on every tool call — MR IDs must be numeric, repo paths must point to real git repos, branch names are sanitized
- Timeouts on all subprocess calls (30-120 seconds) to prevent hangs
- Large diffs are auto-truncated at 10,000 lines to prevent context overflow

When Claude Code launches OmniReview, it automatically spawns the MCP server via `uv` (Python package runner), which resolves the `mcp` dependency on the fly — no manual package installation needed.

**Note:** The MCP server is optional. If you install OmniReview as a personal skill (without the `.mcp.json`), the skill falls back to running the equivalent bash commands directly. The MCP server just makes the process faster, more reliable, and better error-handled.

---

## The Report

After all three agents complete their analysis, OmniReview consolidates their findings into a single structured report:

```
OmniReview Report: !136 — Add staging Stripe configuration

Branch: stripe-config → staging | Author: alice | Pipeline: passed

Summary:
  The change correctly separates staging Stripe keys from production.
  One naming convention inconsistency and a missing validation entry found.

Verdict: APPROVE_WITH_FIXES

Strengths:
  - Clean single-purpose commit with clear message
  - All 20 Stripe variables validated before deployment
  - Secrets loaded from CI/CD variables, never hardcoded

Issues:

  Critical: (none)

  Important:
  1. Missing STRIPE_PRICE_ENTERPRISE_STAGING in placeholder mapping
     .gitlab-ci.yml:1295 | Confidence: 92 | Found by: Codebase + Security

  Minor:
  1. Naming convention inconsistency (STAGING_ prefix vs _STAGING suffix)
     .gitlab-ci.yml:1066-1089 | Confidence: 75 | Found by: MR Analyst

Agent Agreement Matrix:
  .gitlab-ci.yml | MR Analyst: Minor | Codebase: Important | Security: Important
```

### Confidence Scoring

Not all findings are equal. OmniReview assigns each finding a confidence score from 0 to 100:

| Score | What It Means | Included in Report? |
|-------|--------------|---------------------|
| 90-100 | Verified with code evidence — traced the execution path, confirmed the issue | Yes |
| 70-89 | Strong signal — known problematic pattern with clear evidence | Yes |
| 50-69 | Possible issue — might be real, might be a false positive | No (filtered out) |
| Below 50 | Noise — likely a false positive or style nitpick | No (discarded) |

### Cross-Correlation

When multiple agents independently flag the same area, the confidence gets boosted:

- **2 agents agree** → confidence +15 points
- **3 agents agree** → confidence +25 points

This means issues caught by multiple perspectives rise to the top, while single-agent observations are treated with appropriate skepticism.

### False Positive Reduction

OmniReview automatically reduces confidence for common false positive categories:

- Issues that existed *before* this MR (checked via `git blame`)
- Problems that linters or CI would catch anyway
- Pure style preferences with no functional impact
- Issues already discussed and resolved in MR threads

---

## What Happens After Review

OmniReview never takes action without your approval. After presenting the report, you get a structured action menu with **9 options**:

| # | Action | What It Does |
|---|--------|-------------|
| 1 | **Full review post** (Recommended) | Posts a concise overview comment on the MR **plus** individual inline discussion threads on each finding — one thread per issue, placed on the exact diff line. This is the most common action. |
| 2 | **Post summary only** | Posts just the overview comment as a top-level MR note — no inline threads |
| 3 | **Post inline findings only** | Creates only the inline discussion threads on diff lines — no summary comment |
| 4 | **Create GitLab issues** | Opens new GitLab issues for Critical or Important findings, automatically linked back to the MR for traceability |
| 5 | **Approve the MR** | Approves the merge request (only when you explicitly choose this) |
| 6 | **Open in browser** | Opens the MR in your default browser for manual inspection |
| 7 | **Re-review a specific area** | Dispatches a single focused agent to take a deeper look at one particular file or concern |
| 8 | **Verify a concern** | Runs a targeted check on something specific you want validated |
| 9 | **Done** | Finish the review — no further action needed |

**Option 1 is what most people want** — it gives the MR author a high-level overview comment to understand the review at a glance, plus detailed technical threads on the exact lines where issues were found. Each inline thread includes what was found, why it matters, and a specific recommendation with code suggestions where applicable. Multiple threads on the same file are encouraged — each finding gets its own thread so it can be discussed and resolved independently.

You can select multiple actions in sequence. The menu returns after each action until you choose "Done."

All posted comments are written as standard code review text — no AI attribution, no "Generated by" footers.

---

## Installation (Claude Code)

OmniReview currently runs as a **Claude Code personal skill**. The installation places skill files into Claude Code's skills directory, where they are automatically picked up on the next session.

### Prerequisites

1. **Claude Code** installed and working ([get it here](https://claude.ai/code))
2. **uv** (Python package runner) — required for the MCP tool server. Install if not already present:
   ```bash
   # Install uv (macOS/Linux)
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Or via Homebrew (macOS)
   brew install uv

   # Verify installation
   uv --version
   ```
   The MCP server uses `uv run --with mcp[cli]` to auto-resolve Python dependencies at runtime — no manual `pip install` needed.
3. **glab CLI** — required for GitLab MR support. Install and authenticate:
   ```bash
   # Install glab (macOS)
   brew install glab

   # Install glab (Linux)
   # See: https://gitlab.com/gitlab-org/cli#installation

   # Authenticate with your GitLab instance
   glab auth login
   ```
4. **Git** (version 2.15+ for worktree support)
5. A **GitLab repository** cloned locally

### Step-by-Step Installation

#### Option A: Install as Plugin (Recommended)

Since OmniReview follows the official Anthropic plugin format, you can install it directly from GitHub:

```
/plugin install omnireview@github:nexiouscaliver/OmniReview
```

This installs the skill, MCP server, and all agent templates in one step. Claude Code manages updates and uninstall automatically.

#### Option B: Clone and Copy (Manual)

If you prefer a manual installation as a personal skill:

```bash
# 1. Clone the OmniReview repository
git clone https://github.com/nexiouscaliver/OmniReview.git

# 2. Create the Claude Code skills directory with references subdirectory
mkdir -p ~/.claude/skills/omnireview/references

# 3. Copy the skill file
cp OmniReview/skills/omnireview/SKILL.md ~/.claude/skills/omnireview/

# 4. Copy the agent templates and consolidation guide
cp OmniReview/skills/omnireview/references/* ~/.claude/skills/omnireview/references/

# 5. Verify the files are in place
ls -R ~/.claude/skills/omnireview/
# Should show:
#   SKILL.md
#   references/
#     mr-analyst-prompt.md
#     codebase-reviewer-prompt.md
#     security-reviewer-prompt.md
#     consolidation-guide.md

# 6. Clean up (optional)
rm -rf OmniReview
```

**Note:** Manual installation does not include the MCP tool server. The skill will fall back to running bash commands directly, which works but is slower and less error-handled.

#### Option C: Direct Download (Manual)

```bash
# Create Claude Code skills directory structure
mkdir -p ~/.claude/skills/omnireview/references

# Download skill file
curl -sO --output-dir ~/.claude/skills/omnireview \
  "https://raw.githubusercontent.com/nexiouscaliver/OmniReview/main/skills/omnireview/SKILL.md"

# Download reference files
for file in mr-analyst-prompt.md codebase-reviewer-prompt.md \
            security-reviewer-prompt.md consolidation-guide.md; do
  curl -sO --output-dir ~/.claude/skills/omnireview/references \
    "https://raw.githubusercontent.com/nexiouscaliver/OmniReview/main/skills/omnireview/references/$file"
done
```

### After Installation

**Restart your Claude Code session** for the plugin/skill to be detected. Claude Code loads plugins and skills at session start — any running session won't see OmniReview until restarted.

Once restarted, open Claude Code in any GitLab repository and type:

```
/omnireview
```

If the skill is installed correctly, Claude will recognize the command and ask for an MR number.

---

## Usage

### Basic Usage

Navigate to your GitLab project directory and run:

```bash
# Using the slash command
/omnireview 136

# Or just ask naturally
Review MR !136
```

### What to Expect

1. OmniReview will announce itself and start gathering MR data
2. Three git worktrees are created (you'll see them in `.worktrees/`)
3. Three agents are dispatched in parallel — this takes a few minutes
4. A consolidated report appears with findings sorted by severity
5. An action menu lets you choose what to do next
6. Worktrees are automatically cleaned up

### Tips

- **Large MRs** (500+ lines): The review will take longer but is even more valuable — these are exactly the MRs that need multiple perspectives
- **CI/CD changes**: OmniReview's security agent specifically checks pipeline files for secret exposure and script injection
- **Re-review**: If you disagree with a finding, use option 6 to dispatch a single focused agent for a deeper look

---

## Project Structure

```
OmniReview/
  .claude-plugin/
    plugin.json                     # Plugin metadata (name, version, author)
  skills/
    omnireview/
      SKILL.md                      # Main orchestration (7-phase flow)
      references/
        mr-analyst-prompt.md        # MR Analyst agent template
        codebase-reviewer-prompt.md # Codebase Reviewer agent template
        security-reviewer-prompt.md # Security Reviewer agent template
        consolidation-guide.md      # Cross-correlation and report format
  .mcp.json                         # MCP server registration for Claude Code
  tools/
    omnireview_mcp_server.py        # Python MCP server (3 tools, FastMCP)
    requirements.txt                # Python dependencies (mcp>=1.0.0)
  tests/                            # Unit tests (mocked subprocess)
  docs/                             # Design specs and implementation plans
  README.md                         # This file
  CONTRIBUTING.md                   # Contribution guidelines
  CHANGELOG.md                      # Version history
  LICENSE                           # MIT License
```

---

## Roadmap

- [x] Claude Code support (personal skill)
- [x] GitLab MR review via `glab` CLI
- [x] 3 parallel agents with worktree isolation
- [x] Confidence scoring and cross-correlation
- [x] 9-option post-review action menu
- [x] MCP tool server (fetch_mr_data, create/cleanup worktrees)
- [x] Security-hardened subprocess execution (no shell injection)
- [ ] GitHub PR support via `gh` CLI
- [ ] Cursor IDE integration
- [ ] Gemini CLI agent compatibility
- [ ] OpenCode support
- [ ] Kilo Code integration
- [ ] Configurable confidence thresholds
- [ ] Custom agent templates
- [ ] Team-specific review checklists
- [ ] Review history and trend tracking

---

## Contributing

We welcome contributions! Whether it's a bug fix, a new feature, platform support, or documentation improvement — every contribution helps make code reviews better for everyone.

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

**Quick start:**

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/github-support`)
3. Make your changes
4. Submit a pull request

---

## FAQ

**Q: Does OmniReview modify my code?**
No. OmniReview is read-only. It creates temporary git worktrees to examine your code, but never modifies any files. Worktrees are cleaned up automatically after the review.

**Q: Does it post comments automatically?**
Never. OmniReview always presents its findings to you first. You decide what gets posted via the action menu.

**Q: How long does a review take?**
Typically 2-5 minutes depending on the MR size. The three agents run in parallel, so it's roughly the time of one agent, not three.

**Q: What if an agent fails?**
OmniReview is designed for graceful degradation. If one agent fails, the other two still complete, and the gap is noted in the report. If two or more fail, partial results are shown with a recommendation for manual review.

**Q: Can I customize the review focus?**
Yes — the agent prompt templates are fully editable. You can add project-specific checklists, remove sections that don't apply, or adjust the confidence threshold.

**Q: Does it work with self-hosted GitLab?**
Yes, as long as `glab` CLI is configured to point to your instance (`glab config set -g host your-gitlab.example.com`).

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <strong>Built by Shahil Kadia <a href="https://github.com/nexiouscaliver">(@nexiouscaliver)</a></strong>
  <br>
  If OmniReview helps you catch bugs before production, consider giving it a star.
</p>
