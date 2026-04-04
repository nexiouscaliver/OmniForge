<p align="center">
  <h1 align="center">OmniForge</h1>
  <p align="center">
    <em>AI-powered merge request toolkit — review, fix, and create GitLab MRs with multi-agent intelligence</em>
  </p>
  <p align="center">
    <a href="#quick-start">Quick Start</a> &middot;
    <a href="#how-it-works">How It Works</a> &middot;
    <a href="#mcp-tools">MCP Tools</a> &middot;
    <a href="#contributing">Contribute</a>
  </p>
  <p align="center">
    <a href="https://github.com/nexiouscaliver/OmniForge/releases/latest">
      <img src="https://img.shields.io/github/v/release/nexiouscaliver/OmniForge?label=latest&color=brightgreen" alt="Latest Release">
    </a>
    <a href="https://github.com/nexiouscaliver/OmniForge/blob/main/LICENSE">
      <img src="https://img.shields.io/badge/license-MIT-blue" alt="License: MIT">
    </a>
    <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/Claude_Code-Plugin-blueviolet?logo=anthropic&logoColor=white" alt="Claude Code Plugin">
    <img src="https://img.shields.io/badge/MCP_Tools-13-orange" alt="MCP Tools: 13">
    <a href="https://github.com/nexiouscaliver/OmniForge/pulls">
      <img src="https://img.shields.io/badge/PRs-welcome-brightgreen" alt="PRs Welcome">
    </a>
  </p>
</p>

---

## What is OmniForge?

The merge request lifecycle has friction at every stage. Reviews are inconsistent — reviewers get tired, skip files, or rubber-stamp changes. Fixing review findings is tedious manual work. Creating MRs with proper descriptions is a chore nobody enjoys.

**OmniForge automates all three stages.** It is a unified Claude Code plugin that covers the full MR lifecycle: multi-agent adversarial review, automated finding resolution, and MR creation with auto-populated metadata. Three skills, one plugin, zero context switching.

### Included Skills

| Skill | Command | Description |
|-------|---------|-------------|
| OmniReview | `/omnireview-gitlab` | Multi-agent adversarial MR review with 3 parallel agents, confidence scoring, and cross-correlation |
| OmniFix | `/omnifix-gitlab` | Automated finding fixer — triages, applies fixes, verifies, commits, and resolves discussion threads |
| OmniCreate | `/omnicreate-gitlab` | MR creation with auto-populated title and description from commit history |

---

## Quick Start

### Prerequisites

1. **Claude Code** installed and working ([get it here](https://claude.ai/code))
2. **uv** (Python package runner) — required for the MCP tool server:
   ```bash
   # macOS/Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Or via Homebrew (macOS)
   brew install uv

   # Verify
   uv --version
   ```
   The MCP server uses `uv run --with mcp[cli]` to auto-resolve Python dependencies at runtime — no manual `pip install` needed.
3. **glab CLI** — required for GitLab MR operations:
   ```bash
   # macOS
   brew install glab

   # Linux — see https://gitlab.com/gitlab-org/cli#installation

   # Authenticate
   glab auth login
   ```
4. **Git** (version 2.15+ for worktree support)
5. A **GitLab repository** cloned locally

### Installation

#### Option A: Install as Plugin (Recommended)

Two commands in your terminal (outside Claude Code):

```bash
# 1. Add OmniForge as a marketplace source
claude plugin marketplace add https://github.com/nexiouscaliver/OmniForge.git

# 2. Install the plugin
claude plugin install omniforge@omniforge-marketplace
```

This installs all three skills, the MCP server, and all agent templates in one step. Claude Code manages updates and uninstall automatically.

To update later:
```bash
claude plugin marketplace update omniforge-marketplace
claude plugin update omniforge
```

To uninstall:
```bash
claude plugin uninstall omniforge
claude plugin marketplace remove omniforge-marketplace
```

#### Option B: Load from Local Directory (Development/Testing)

If you have cloned the repo locally and want to test without installing:

```bash
claude --plugin-dir /path/to/OmniForge/plugins/omniforge
```

#### Option C: Manual Personal Skill (No MCP Server)

If you prefer a manual installation as a personal skill (without the MCP tool server):

```bash
# 1. Clone the repository
git clone https://github.com/nexiouscaliver/OmniForge.git

# 2. Install the review skill
mkdir -p ~/.claude/skills/omnireview-gitlab/references
cp OmniForge/plugins/omniforge/skills/omnireview-gitlab/SKILL.md ~/.claude/skills/omnireview-gitlab/
cp OmniForge/plugins/omniforge/skills/omnireview-gitlab/references/* ~/.claude/skills/omnireview-gitlab/references/

# 3. Install OmniFix (fix skill)
mkdir -p ~/.claude/skills/omnifix-gitlab/references
cp OmniForge/plugins/omniforge/skills/omnifix-gitlab/SKILL.md ~/.claude/skills/omnifix-gitlab/
cp OmniForge/plugins/omniforge/skills/omnifix-gitlab/references/* ~/.claude/skills/omnifix-gitlab/references/

# 4. Install OmniCreate (create skill)
mkdir -p ~/.claude/skills/omnicreate-gitlab
cp OmniForge/plugins/omniforge/skills/omnicreate-gitlab/SKILL.md ~/.claude/skills/omnicreate-gitlab/

# 5. Clean up
rm -rf OmniForge
```

**Note:** Manual installation does not include the MCP tool server. Skills fall back to running bash commands directly, which works but is slower and less error-handled.

### After Installation

**Restart your Claude Code session** for the plugin to be detected. Claude Code loads plugins at session start — any running session will not see OmniForge until restarted.

Once restarted, open Claude Code in any GitLab repository and run your first review:

```
/omnireview-gitlab 136
```

---

## How It Works

### `/omnireview-gitlab` — Multi-Agent MR Review

OmniForge runs three independent AI review agents simultaneously, each in its own isolated copy of your codebase via [git worktrees](https://git-scm.com/docs/git-worktree). Each agent examines the merge request from a different angle. When they finish, their findings are cross-referenced, scored for confidence, filtered for false positives, and delivered as a single consolidated report.

#### The Three Agents

**MR Analyst** — *"Is this merge request well-crafted?"*

Focuses on process quality — the things that have nothing to do with code but everything to do with whether a change is safe to merge:
- Examines every commit — are messages clear? Is each commit atomic?
- Checks the MR description — does it explain what changed and why?
- Reviews all discussion threads — are reviewer concerns addressed or left hanging?
- Evaluates scope — is this MR focused, or is it sneaking in unrelated changes?
- Verifies CI/CD pipeline status

**Codebase Reviewer** — *"Is this code correct, clean, and well-integrated?"*

Performs a deep code review that goes beyond the diff. It has full access to your codebase and traces call chains, checks test coverage, and verifies architectural consistency:
- Reads the complete files that were changed (not just the diff lines)
- Traces imports, callers, and dependencies to understand impact
- Checks for logic errors, edge cases, and race conditions
- Verifies test coverage — are new features actually tested?
- Evaluates architecture — does this change fit existing codebase patterns?
- Flags performance concerns like N+1 queries or blocking async calls

**Security Reviewer** — *"Can this change be exploited?"*

Thinks like an attacker. Systematically walks through the OWASP Top 10 checklist and looks for vulnerabilities both in the changes and in how they interact with existing code:
- **Injection** — SQL, XSS, command injection, path traversal
- **Broken access control** — missing authorization, privilege escalation
- **Cryptographic failures** — hardcoded secrets, weak algorithms
- **Authentication issues** — JWT validation, session handling
- **Data exposure** — PII leaks, overly broad API responses
- **SSRF, misconfigurations, vulnerable dependencies**, and more
- Scans for hardcoded secrets, API keys, and credentials in the diff and commit history

#### 7-Phase Review Pipeline

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
|                |  |  Reviewer      |  |  Reviewer      |
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

Each agent gets its own complete copy of the repository through git worktrees. This means agents can freely navigate the codebase without interfering with each other, each sees the exact state of the MR source branch, your working directory is never touched, and everything is cleaned up automatically when the review finishes.

#### Confidence Scoring

Each finding receives a confidence score from 0 to 100:

| Score | Meaning | Included in Report? |
|-------|---------|---------------------|
| 90-100 | Verified with code evidence — traced the execution path, confirmed the issue | Yes |
| 70-89 | Strong signal — known problematic pattern with clear evidence | Yes |
| 50-69 | Possible issue — might be real, might be a false positive | No (filtered out) |
| Below 50 | Noise — likely a false positive or style nitpick | No (discarded) |

#### Cross-Correlation

When multiple agents independently flag the same area, confidence is boosted:

- **2 agents agree** — confidence +15 points
- **3 agents agree** — confidence +25 points

Issues caught by multiple perspectives rise to the top, while single-agent observations are treated with appropriate skepticism.

#### False Positive Reduction

OmniForge automatically reduces confidence for common false positive categories:

- Issues that existed before this MR (checked via `git blame`)
- Problems that linters or CI would catch anyway
- Pure style preferences with no functional impact
- Issues already discussed and resolved in MR threads

#### Report Format

```
OmniForge Report: !136 — Add staging Stripe configuration

Branch: stripe-config -> staging | Author: alice | Pipeline: passed

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

#### Post-Review Action Menu

OmniForge never takes action without your approval. After presenting the report, you get a structured action menu with 9 options:

| # | Action | What It Does |
|---|--------|-------------|
| 1 | **Full review post** (Recommended) | Posts a concise overview comment on the MR plus individual inline discussion threads on each finding — one thread per issue, placed on the exact diff line |
| 2 | **Post summary only** | Posts just the overview comment as a top-level MR note — no inline threads |
| 3 | **Post inline findings only** | Creates only the inline discussion threads on diff lines — no summary comment |
| 4 | **Create GitLab issues** | Opens new GitLab issues for Critical or Important findings, automatically linked back to the MR |
| 5 | **Approve the MR** | Approves the merge request (only when you explicitly choose this) |
| 6 | **Open in browser** | Opens the MR in your default browser for manual inspection |
| 7 | **Re-review a specific area** | Dispatches a single focused agent to take a deeper look at one particular file or concern |
| 8 | **Verify a concern** | Runs a targeted check on something specific you want validated |
| 9 | **Done** | Finish the review — no further action needed |

Option 1 is the most common choice — it gives the MR author a high-level overview plus detailed technical threads on the exact lines where issues were found. Each inline thread includes what was found, why it matters, and a specific recommendation with code suggestions where applicable. You can select multiple actions in sequence. The menu returns after each action until you choose "Done."

All posted comments are written as standard code review text — no AI attribution or "Generated by" footers.

### `/omnifix-gitlab` — Automated Finding Fixer

OmniForge finds issues. OmniFix fixes them. The companion skill automates the entire fix cycle after a review.

```
/omnifix-gitlab 136
```

#### 7-Phase Fix Pipeline

```
Unresolved findings on MR !136
    |
    +-- Phase 1: Fetch all discussion threads
    +-- Phase 2: Triage (parallel subagents validate each finding)
    +-- Phase 3: User approves which to fix (mandatory gate)
    +-- Phase 4: Fix agent applies changes sequentially
    +-- Phase 5: Verification agent reviews all changes
    +-- Phase 6: Commit, push, reply on threads, resolve discussions
    +-- Phase 7: Cleanup worktrees
```

**Key features:**
- **Parallel triage** — N subagents validate N findings independently (VALID / INVALID / NEEDS_HUMAN)
- **User approval gate** — no code changes without explicit consent
- **Sequential fixing** — prevents file conflicts between overlapping fixes
- **Verification agent** — fresh-eyes review catches regressions before commit
- **Thread resolution** — replies "Fixed in {sha}" and optionally resolves discussions
- **25-finding cap** — prevents runaway costs on large MRs

### `/omnicreate-gitlab` — MR Creation

Automates GitLab merge request creation using the `glab` CLI with auto-populated title and description from commits.

```
/omnicreate-gitlab
```

**What it does:**
- Auto-populates MR title from the first commit message
- Auto-populates description from all commit messages (including bodies)
- Pushes the branch if needed
- Supports draft MRs, labels, assignees, reviewers, and more

**Example invocations:**
```bash
# Create MR from current branch
/omnicreate-gitlab

# Create draft MR with labels
/omnicreate-gitlab --draft -l bug,needs-review

# Create MR for a specific issue
/omnicreate-gitlab -i 42 --copy-issue-labels

# Create MR targeting staging branch, assign to user
/omnicreate-gitlab -b staging -a john
```

**Prerequisites:** `glab` CLI installed and authenticated, on a feature branch (not main/master), with commits to create an MR from.

---

## MCP Tools

OmniForge includes a Python [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that runs as a local child process alongside Claude Code. The server exposes 13 dedicated tools that the skills call directly:

| Tool | What It Does | Used By |
|------|-------------|---------|
| `mcp__omniforge__fetch_mr_data` | Fetches MR metadata, comments, diff, commits, file list, and diff line map | Review + Fix |
| `mcp__omniforge__create_review_worktrees` | Creates 3 isolated worktrees for review agents | Review |
| `mcp__omniforge__cleanup_review_worktrees` | Removes review worktrees with fallback cleanup | Review |
| `mcp__omniforge__post_full_review` | Posts summary comment + inline discussion threads for all findings | Review |
| `mcp__omniforge__post_review_summary` | Posts a top-level MR overview comment | Review + Fix |
| `mcp__omniforge__post_inline_thread` | Posts an inline discussion thread on a specific diff line | Review |
| `mcp__omniforge__create_linked_issue` | Creates a GitLab issue linked to the source MR | Review + Fix |
| `mcp__omniforge__map_diff_lines` | Parses a diff and returns exact changed line numbers per file | Review + Fix |
| `mcp__omniforge__fetch_mr_discussions` | Fetches all discussion threads with positions, bodies, and resolved status | Fix |
| `mcp__omniforge__reply_to_discussion` | Posts a reply to a specific discussion thread | Fix |
| `mcp__omniforge__resolve_discussion` | Resolves or unresolves a discussion thread | Fix |
| `mcp__omniforge__cleanup_omnifix_worktrees` | Removes OmniFix worktrees and temp branches | Fix |
| `mcp__omniforge__create_gitlab_mr` | Creates a GitLab merge request with auto-populated metadata | Create |

### Security Hardening

The MCP server is security-hardened and performance-optimized:

- **Injection Protection:** All subprocess calls use `create_subprocess_exec` (argument list, no shell interpretation).
- **Resilient Decoding:** Subprocess output uses `errors="replace"` to prevent crashes on non-UTF-8 characters.
- **Efficient Posting:** `post_full_review` fetches MR metadata once and reuses it for all inline threads, reducing API latency.
- **Accurate Line Mapping:** `map_diff_lines` parses hunk headers to return exact added/modified line numbers per file, so inline threads always land on valid diff lines.
- **Auto-Truncation:** Large diffs are capped at 10,000 lines to prevent context overflow while agents explore full files in worktrees.

When Claude Code launches OmniForge, it automatically spawns the MCP server via `uv` (Python package runner), which resolves the `mcp` dependency on the fly — no manual package installation needed.

**Note:** The MCP server is optional. If you install OmniForge as a personal skill (without the `.mcp.json`), skills fall back to running the equivalent bash commands directly. The MCP server makes the process faster, more reliable, and better error-handled.

---

## Platform Support

### Currently Supported

| Platform | Tool | Status |
|----------|------|--------|
| **GitLab** merge requests | `glab` CLI | Supported |
| **Claude Code** (Anthropic) | Plugin (marketplace) | Supported |

### Roadmap

Support for additional platforms and AI coding tools is planned:

- **GitHub** pull requests via `gh` CLI
- **Cursor** IDE integration
- **Gemini CLI** (Google) agent compatibility
- **OpenCode** support
- **Kilo Code** integration
- **Other AI coding assistants** as the ecosystem evolves

---

## Project Structure

```
OmniForge/                                          # Marketplace root
  .claude-plugin/
    marketplace.json                                # Marketplace registry (lists plugins)
  plugins/
    omniforge/                                      # The plugin
      .claude-plugin/
        plugin.json                                 # Plugin metadata (name, version, author)
      skills/
        omnireview-gitlab/                          # Review skill (7-phase review flow)
          SKILL.md
          references/                               # 3 agent prompts + consolidation guide
        omnifix-gitlab/                             # Fix skill (7-phase fix flow)
          SKILL.md
          references/                               # Triage, fix, verify agent prompts
        omnicreate-gitlab/                          # MR creation skill (glab CLI, MCP-powered)
          SKILL.md
      .mcp.json                                     # MCP server registration
      tools/
        omniforge_mcp_server.py                     # Python MCP server (13 tools, FastMCP)
        requirements.txt                            # Python dependencies (mcp>=1.0.0)
      tests/                                        # Unit tests (116 tests, mocked subprocess)
  docs/                                             # Design specs and implementation plans
  README.md                                         # This file
  CONTRIBUTING.md                                   # Contribution guidelines
  CHANGELOG.md                                      # Version history
  LICENSE                                           # MIT License
```

---

## FAQ

**Q: Does OmniForge modify my code?**
The review skill (`/omnireview-gitlab`) is read-only. It creates temporary git worktrees to examine your code but never modifies any files. Worktrees are cleaned up automatically after the review. The fix skill (`/omnifix-gitlab`) does modify code — it applies fixes to resolve review findings, commits them, and pushes to the MR branch. No changes are made without your explicit approval through the mandatory user approval gate. The create skill (`/omnicreate-gitlab`) does not modify code — it only creates a merge request from existing commits.

**Q: Does it post comments automatically?**
Never. OmniForge always presents findings to you first. You decide what gets posted via the action menu. OmniFix replies on threads only after applying approved fixes.

**Q: How long does a review take?**
Typically 2-5 minutes depending on MR size. The three agents run in parallel, so it is roughly the time of one agent, not three.

**Q: What if an agent fails?**
OmniForge is designed for graceful degradation. If one agent fails, the other two still complete, and the gap is noted in the report. If two or more fail, partial results are shown with a recommendation for manual review.

**Q: Can I customize the review focus?**
Yes. The agent prompt templates are fully editable. You can add project-specific checklists, remove sections that do not apply, or adjust the confidence threshold.

**Q: Does it work with self-hosted GitLab?**
Yes, as long as `glab` CLI is configured to point to your instance (`glab config set -g host your-gitlab.example.com`).

---

## Contributing

Contributions are welcome. Whether it is a bug fix, a new feature, platform support, or documentation improvement — every contribution helps.

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

**Quick start:**

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/github-support`)
3. Make your changes
4. Submit a pull request

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <strong>Built by <a href="https://github.com/nexiouscaliver">@nexiouscaliver</a></strong>
</p>
