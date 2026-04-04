# Contributing to OmniReview

Thank you for considering contributing to OmniReview. Every contribution — whether it's a bug report, feature request, documentation fix, or code change — helps make code reviews better for everyone.

## Table of Contents

- [Getting Started](#getting-started)
- [How to Contribute](#how-to-contribute)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Writing Agent Prompts](#writing-agent-prompts)
- [Adding Platform Support](#adding-platform-support)
- [Style Guide](#style-guide)
- [Pull Request Process](#pull-request-process)
- [Code of Conduct](#code-of-conduct)

---

## Getting Started

### What Can I Contribute?

| Contribution Type | Examples |
|-------------------|---------|
| **Bug reports** | Agent produces incorrect findings, worktree cleanup fails, glab commands error |
| **Platform support** | GitHub PR support, Cursor integration, Gemini CLI compatibility |
| **Agent improvements** | Better prompts, additional checklist items, reduced false positives |
| **Documentation** | Installation guides, usage examples, FAQ entries |
| **Testing** | Pressure scenarios, baseline tests, edge case coverage |
| **Tooling** | Installation scripts, update mechanisms, configuration tools |

### First-Time Contributors

Look for issues labeled `good first issue` — these are designed to be approachable for newcomers. Documentation improvements are always a great starting point.

---

## How to Contribute

### Reporting Bugs

1. Check [existing issues](https://github.com/nexiouscaliver/OmniReview/issues) to avoid duplicates
2. Open a new issue with:
   - **What happened** — describe the unexpected behavior
   - **What you expected** — what should have happened
   - **Steps to reproduce** — how can we see the same issue
   - **Environment** — OS, Claude Code version, glab version, git version
   - **Logs/output** — any relevant error messages or agent output

### Suggesting Features

Open an issue with the `feature request` label. Include:
- **The problem** — what pain point does this address?
- **Your proposed solution** — how would it work?
- **Alternatives considered** — what else did you think about?

### Submitting Changes

1. **Fork** the repository
2. **Create a branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes** (see sections below for guidance)
4. **Test your changes** (see [Testing](#testing))
5. **Commit** with a clear message:
   ```bash
   git commit -m "feat: add GitHub PR support via gh CLI"
   ```
6. **Push** and open a **Pull Request** against `main`

---

## Development Setup

OmniReview is a plugin with Markdown skill files and a Python MCP server.

```bash
# Clone your fork
git clone https://github.com/YOUR-USERNAME/OmniReview.git
cd OmniReview

# Load as plugin for testing (session-only, no permanent install)
claude --plugin-dir plugins/omnireview

# Or install permanently for testing
claude plugin marketplace add .
claude plugin install omnireview@omnireview-marketplace
```

### Running Unit Tests

The MCP server has 43 unit tests with mocked subprocess calls:

```bash
cd plugins/omnireview
python -m pytest tests/ -v
```

### Skill Testing

OmniReview follows a TDD (Test-Driven Development) approach adapted for AI skills:

1. **RED** — Run the skill against a real MR and document where it fails or produces poor results
2. **GREEN** — Modify the skill files to address the specific failures
3. **REFACTOR** — Identify remaining loopholes and close them

To test your changes:

1. Load the plugin: `claude --plugin-dir plugins/omnireview`
2. Open Claude Code in a GitLab repository
3. Run `/omnireview {MR_NUMBER}` against a real MR
4. Verify all 7 phases execute correctly
5. Check that your changes produce the expected improvement

---

## Project Structure

```
OmniReview/
  .claude-plugin/
    marketplace.json                  # Marketplace registry
  plugins/omnireview/                 # The plugin
    .claude-plugin/plugin.json        # Plugin metadata
    skills/
      omnireview-gitlab/
        SKILL.md                      # Review skill (7-phase review flow)
        references/
          mr-analyst-prompt.md        # MR Analyst agent template
          codebase-reviewer-prompt.md # Codebase Reviewer agent template
          security-reviewer-prompt.md # Security Reviewer agent template
          consolidation-guide.md      # Consolidation algorithm and report format
      omnifix-gitlab/
        SKILL.md                      # Fix skill (7-phase fix flow)
        references/
          triage-agent-prompt.md      # Triage agent template
          fix-agent-prompt.md         # Fix agent template
          verify-agent-prompt.md      # Verify agent template
      omnicreate-gitlab/
        SKILL.md                      # MR creation skill (glab CLI, MCP-powered)
    .mcp.json                         # MCP server registration
    tools/
      omnireview_mcp_server.py        # Python MCP server (13 tools)
      requirements.txt
    tests/                            # Unit tests (92+ tests)
```

### SKILL.md

The main entry point at `plugins/omnireview/skills/omnireview-gitlab/SKILL.md`. Contains the 7-phase workflow, error handling, action menu, rationalization defenses, and integration references. This is what Claude reads when the skill is invoked.

### Agent Prompt Templates

Files in `plugins/omnireview/skills/omnireview-gitlab/references/`. Each `*-prompt.md` is a template filled with MR data and sent to a subagent. Templates use `{PLACEHOLDER}` syntax for variable injection.

### MCP Tool Server

`plugins/omnireview/tools/omnireview_mcp_server.py` — a Python FastMCP server exposing 13 tools. Registered via `plugins/omnireview/.mcp.json`. Claude Code spawns it automatically using `uv`.

### Consolidation Guide

`plugins/omnireview/skills/omnireview-gitlab/references/consolidation-guide.md` — the algorithm for merging findings from all three agents.

---

## Writing Agent Prompts

Agent prompts are the core of OmniReview. When modifying or creating prompts:

### Do

- **Be specific** — tell the agent exactly what to check and how
- **Require evidence** — every finding must have a file:line reference
- **Include confidence scoring** — use the 0-100 scale consistently
- **Define false positives** — help the agent filter noise
- **Set the adversarial stance** — agents should assume code has problems until proven otherwise

### Don't

- **Don't be vague** — "check for bugs" is not actionable; "check for off-by-one errors in loop bounds" is
- **Don't skip the Deep Dive Protocol** — agents must read full files, not just diffs
- **Don't remove the false positive check** — it's what prevents noisy reports
- **Don't allow auto-posting** — agents must only report findings, never take action

### Template Placeholders

| Placeholder | Content |
|-------------|---------|
| `{MR_ID}` | Merge request number |
| `{MR_TITLE}` | MR title |
| `{WORKTREE_PATH}` | Absolute path to agent's worktree |
| `{MR_JSON_DATA}` | Full JSON metadata from glab |
| `{MR_DESCRIPTION}` | MR description text |
| `{MR_COMMENTS}` | All discussion threads |
| `{MR_DIFF}` | Raw diff output |
| `{COMMIT_LIST}` | Commit SHAs and messages |
| `{FILES_CHANGED_LIST}` | List of changed file paths |
| `{SOURCE_BRANCH}` | MR source branch |
| `{TARGET_BRANCH}` | MR target branch |

---

## Adding Platform Support

The highest-impact contributions right now are **new platform integrations**. Here's how to add support for a new platform:

### GitHub Support (Example)

1. Create a new skill directory `plugins/omnireview/skills/omnireview-github/` with a variant `SKILL.md` that uses `gh` instead of `glab`:
   - Replace `glab mr view` with `gh pr view`
   - Replace `glab mr diff` with `gh pr diff`
   - Replace `glab mr note` with `gh pr comment`
   - Update MCP tools in `plugins/omnireview/tools/omnireview_mcp_server.py` to support `gh` calls
   - Update action menu commands accordingly

2. Agent prompt templates in `plugins/omnireview/skills/omnireview-gitlab/references/` can be largely reused — the core review logic is platform-agnostic. Only the MR-specific terminology needs changing (MR → PR, merge request → pull request).

3. Add installation instructions for the new platform.

### IDE/Tool Integration

For tools like Cursor, Gemini CLI, OpenCode, or Kilo Code:

1. Research how the tool supports custom skills/prompts/agents
2. Adapt the SKILL.md workflow to the tool's dispatch mechanism
3. Ensure worktree creation and cleanup work in the tool's environment
4. Document any limitations or differences

---

## Style Guide

### Markdown

- Use ATX-style headers (`##`, not underlines)
- Use fenced code blocks with language identifiers
- Tables for structured data, bullet lists for sequences
- One sentence per line in source (for cleaner diffs)

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add GitHub PR support via gh CLI
fix: handle worktree cleanup when branch name has slashes
docs: add FAQ entry about self-hosted GitLab
refactor: simplify confidence scoring algorithm
```

### Agent Prompt Style

- Imperative mood for instructions ("Check for...", "Verify that...")
- Bold for emphasis on critical instructions
- Tables for structured checklists
- Clear section headers with consistent hierarchy

---

## Pull Request Process

1. **One concern per PR** — don't mix platform support with prompt improvements
2. **Describe what and why** — the PR description should explain the change and its motivation
3. **Test on a real MR** — include the MR number you tested against and a summary of results
4. **Update documentation** — if your change affects usage, update the README
5. **Keep it focused** — small, reviewable PRs are merged faster than large ones

### PR Checklist

- [ ] Changes tested against a real merge request
- [ ] All 7 phases still execute correctly
- [ ] Worktree cleanup verified (no stale worktrees after review)
- [ ] README updated if user-facing changes
- [ ] Commit messages follow conventional commits

---

## Code of Conduct

Be respectful, constructive, and inclusive. We're all here to make code reviews better.

- Be kind in issue discussions and PR reviews
- Assume good intent
- Focus on the work, not the person
- Welcome newcomers — everyone was new once

---

## Questions?

Open an issue with the `question` label, or start a discussion in the repository's Discussions tab.

Thank you for helping make OmniReview better.
