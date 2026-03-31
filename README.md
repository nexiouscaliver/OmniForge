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
  <p align="center">
    <a href="https://github.com/nexiouscaliver/OmniReview/releases/latest">
      <img src="https://img.shields.io/github/v/release/nexiouscaliver/OmniReview?label=latest&color=brightgreen" alt="Latest Release">
    </a>
    <a href="https://github.com/nexiouscaliver/OmniReview/blob/main/LICENSE">
      <img src="https://img.shields.io/badge/license-MIT-blue" alt="License: MIT">
    </a>
    <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/Claude_Code-Plugin-blueviolet?logo=anthropic&logoColor=white" alt="Claude Code Plugin">
    <img src="https://img.shields.io/badge/MCP_Tools-12-orange" alt="12 MCP Tools">
    <a href="https://github.com/nexiouscaliver/OmniReview/pulls">
      <img src="https://img.shields.io/badge/PRs-welcome-brightgreen" alt="PRs Welcome">
    </a>
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
| **Claude Code** (Anthropic) | Plugin (marketplace) | Supported |

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

OmniReview includes a Python [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that runs as a local child process alongside Claude Code. The MCP server exposes **12 dedicated tools** that both skills (OmniReview + OmniFix) call directly:

| Tool | What It Does | Used By |
|------|-------------|---------|
| `fetch_mr_data` | Fetches MR metadata, comments, diff, commits, file list, and diff line map | Review + Fix |
| `create_review_worktrees` | Creates 3 isolated worktrees for review agents | Review |
| `cleanup_review_worktrees` | Removes review worktrees with fallback cleanup | Review |
| `post_full_review` | Posts summary comment + inline discussion threads for all findings | Review |
| `post_review_summary` | Posts a top-level MR overview comment | Review + Fix |
| `post_inline_thread` | Posts an inline discussion thread on a specific diff line | Review |
| `create_linked_issue` | Creates a GitLab issue linked to the source MR | Review + Fix |
| `map_diff_lines` | Parses a diff and returns exact changed line numbers per file | Review + Fix |
| `fetch_mr_discussions` | Fetches all discussion threads with positions, bodies, and resolved status | Fix |
| `reply_to_discussion` | Posts a reply to a specific discussion thread | Fix |
| `resolve_discussion` | Resolves or unresolves a discussion thread | Fix |
| `cleanup_omnifix_worktrees` | Removes OmniFix worktrees and temp branches | Fix |

The MCP server is security-hardened and performance-optimized:
- **Injection Protection:** All subprocess calls use `create_subprocess_exec` (argument list, no shell interpretation).
- **Resilient Decoding:** Subprocess output uses `errors="replace"` to prevent crashes on non-UTF-8 characters.
- **Efficient Posting:** `post_full_review` fetches MR metadata once and reuses it for all inline threads, drastically reducing API latency.
- **Accurate Line Mapping:** `map_diff_lines` parses hunk headers to return exact added/modified line numbers per file, so inline threads always land on valid diff lines — even when cheaper models are used.
- **Auto-Truncation:** Large diffs are capped at 10,000 lines to prevent context overflow while agents explore full files in worktrees.

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

## OmniFix: Automated Finding Fixer

OmniReview finds issues. **OmniFix fixes them.**

The companion skill `omnifix-gitlab` automates the entire fix cycle after a review. It fetches unresolved discussion threads, validates each finding with parallel triage subagents, applies fixes with user approval, verifies changes, and posts resolution replies — all in a single command.

```
/omnifix-gitlab 136
```

### How OmniFix Works

```
Unresolved findings on MR !136
    │
    ├── Phase 1: Fetch all discussion threads
    ├── Phase 2: Triage (parallel subagents validate each finding)
    ├── Phase 3: User approves which to fix (mandatory gate)
    ├── Phase 4: Fix agent applies changes sequentially
    ├── Phase 5: Verification agent reviews all changes
    ├── Phase 6: Commit, push, reply on threads, resolve discussions
    └── Phase 7: Cleanup worktrees
```

**Key features:**
- **Parallel triage** — N subagents validate N findings independently (VALID / INVALID / NEEDS_HUMAN)
- **User approval gate** — no code changes without explicit consent
- **Sequential fixing** — prevents file conflicts between overlapping fixes
- **Verification agent** — fresh-eyes review catches regressions before commit
- **Thread resolution** — replies "Fixed in {sha}" and optionally resolves discussions
- **25-finding cap** — prevents runaway costs on large MRs

---

## Installation (Claude Code)

OmniReview is a **Claude Code plugin** distributed as its own marketplace. Installation takes two commands — add the marketplace, then install the plugin.

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

Two commands in your terminal (outside Claude Code):

```bash
# 1. Add OmniReview as a marketplace source
claude plugin marketplace add https://github.com/nexiouscaliver/OmniReview.git

# 2. Install the plugin
claude plugin install omnireview@omnireview-marketplace
```

This installs the skill, MCP server, and all agent templates in one step. Claude Code manages updates and uninstall automatically.

To update later:
```bash
claude plugin marketplace update omnireview-marketplace
claude plugin update omnireview
```

To uninstall:
```bash
claude plugin uninstall omnireview
claude plugin marketplace remove omnireview-marketplace
```

#### Option B: Load from Local Directory (Development/Testing)

If you've cloned the repo locally and want to test without installing:

```bash
# Start Claude Code with OmniReview loaded for this session only
claude --plugin-dir /path/to/OmniReview/plugins/omnireview
```

#### Option C: Manual Personal Skill (No MCP Server)

If you prefer a manual installation as a personal skill (without the MCP tool server):

```bash
# 1. Clone the OmniReview repository
git clone https://github.com/nexiouscaliver/OmniReview.git

# 2. Install OmniReview (review skill)
mkdir -p ~/.claude/skills/omnireview-gitlab/references
cp OmniReview/plugins/omnireview/skills/omnireview-gitlab/SKILL.md ~/.claude/skills/omnireview-gitlab/
cp OmniReview/plugins/omnireview/skills/omnireview-gitlab/references/* ~/.claude/skills/omnireview-gitlab/references/

# 3. Install OmniFix (fix skill)
mkdir -p ~/.claude/skills/omnifix-gitlab/references
cp OmniReview/plugins/omnireview/skills/omnifix-gitlab/SKILL.md ~/.claude/skills/omnifix-gitlab/
cp OmniReview/plugins/omnireview/skills/omnifix-gitlab/references/* ~/.claude/skills/omnifix-gitlab/references/

# 4. Clean up
rm -rf OmniReview
```

**Note:** Manual installation does not include the MCP tool server. Skills fall back to running bash commands directly, which works but is slower and less error-handled.

### After Installation

**Restart your Claude Code session** for the plugin to be detected. Claude Code loads plugins at session start — any running session won't see OmniReview until restarted.

Once restarted, open Claude Code in any GitLab repository and type:

```
/omnireview
```

If the skill is installed correctly, Claude will recognize the command and ask for an MR number.

---

## Included Skills

OmniReview includes three complementary skills for GitLab workflows:

### `/omnireview` — MR Review

Multi-agent adversarial review of merge requests. See [How It Works](#how-it-works) for details.

### `/omnifix` — Automated Fix

Automated review finding fixer — triages findings, applies fixes, verifies, and resolves discussion threads.

### `/omnicreate` — MR Creation
Automates GitLab merge request creation using the `glab` CLI with auto-populated title and description from commits.

**Basic usage:**
```bash
/omnicreate
```

**What it does:**
- Auto-populates MR title from the first commit message
- Auto-populates description from all commit messages (including bodies)
- Pushes the branch if needed
- Supports draft MRs, labels, assignees, reviewers, and more

**Example invocations:**
```bash
# Create MR from current branch
/omnicreate

# Create draft MR with labels
/omnicreate --draft -l bug,needs-review

# Create MR for a specific issue
/omnicreate -i 42 --copy-issue-labels

# Create MR targeting staging branch, assign to user
/omnicreate -b staging -a john
```

**Prerequisites:**
- `glab` CLI installed and authenticated
- On a feature branch (not main/master)
- Has commits to create MR from

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
OmniReview/                                         # Marketplace root
  .claude-plugin/
    marketplace.json                                # Marketplace registry (lists plugins)
  plugins/
    omnireview/                                     # The plugin
      .claude-plugin/
        plugin.json                                 # Plugin metadata (name, version, author)
      skills/
        omnireview-gitlab/                          # Review skill (7-phase review flow)
          SKILL.md
          references/                               # 3 agent prompts + consolidation guide
        omnifix-gitlab/                             # Fix skill (7-phase fix flow)
          SKILL.md
          references/                               # Triage, fix, verify agent prompts
        omnicreate/
          SKILL.md                                  # MR creation skill (glab CLI, MCP-powered)
      .mcp.json                                     # MCP server registration
      tools/
        omnireview_mcp_server.py                    # Python MCP server (13 tools, FastMCP)
        requirements.txt                            # Python dependencies (mcp>=1.0.0)
      tests/                                        # Unit tests (92 tests, mocked subprocess)
  docs/                                             # Design specs and implementation plans
  README.md                                         # This file
  CONTRIBUTING.md                                   # Contribution guidelines
  CHANGELOG.md                                      # Version history
  LICENSE                                           # MIT License
```

---

## Roadmap

- [x] Claude Code plugin (marketplace install)
- [x] GitLab MR review via `glab` CLI
- [x] GitLab MR creation skill (`/omnicreate` with MCP tool)
- [x] 3 parallel agents with worktree isolation
- [x] Confidence scoring and cross-correlation
- [x] 9-option post-review action menu
- [x] MCP tool server (13 tools: fetch, worktrees, posting, issues, diff mapping, discussions, fix cleanup, MR creation)
- [x] Security-hardened subprocess execution (no shell injection)
- [x] Diff line mapping for accurate inline thread placement
- [x] OmniFix: automated review finding fixer (triage, fix, verify, resolve)
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
**OmniReview** (the review skill) is read-only. It creates temporary git worktrees to examine your code, but never modifies any files. Worktrees are cleaned up automatically after the review.
**OmniFix** (the fix skill) *does* modify code — it applies fixes to resolve review findings, commits them, and pushes to the MR branch. No changes are made without your explicit approval through the mandatory user approval gate.

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
