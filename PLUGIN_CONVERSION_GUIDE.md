# Converting OmniReview to an Official Claude Code Plugin

This document captures everything needed to convert OmniReview from a personal Claude Code skill into an official Anthropic plugin listed in the [claude-plugins-official](https://github.com/anthropics/claude-plugins-official) directory.

---

## Table of Contents

- [Overview](#overview)
- [Two Paths to Publishing](#two-paths-to-publishing)
- [Plugin Structure Requirements](#plugin-structure-requirements)
- [Current vs Target Structure](#current-vs-target-structure)
- [plugin.json Specification](#pluginjson-specification)
- [Skill File Format](#skill-file-format)
- [SKILL.md Frontmatter Reference](#skillmd-frontmatter-reference)
- [Supporting Files Organization](#supporting-files-organization)
- [Optional Plugin Features](#optional-plugin-features)
- [How Claude Code Loads Plugins](#how-claude-code-loads-plugins)
- [Submission Process](#submission-process)
- [Installation by End Users](#installation-by-end-users)
- [Reference: Official Example Plugin](#reference-official-example-plugin)
- [Reference: Marketplace Registry Format](#reference-marketplace-registry-format)
- [Reference: Existing Plugins in Directory](#reference-existing-plugins-in-directory)
- [Conversion Checklist](#conversion-checklist)

---

## Overview

Claude Code supports a plugin system that allows skills, commands, agents, hooks, and MCP servers to be packaged, distributed, and installed by any Claude Code user. Plugins are published to the [claude-plugins-official](https://github.com/anthropics/claude-plugins-official) marketplace maintained by Anthropic.

The key difference between a personal skill and a plugin:

| Aspect | Personal Skill | Plugin |
|--------|---------------|--------|
| Location | `~/.claude/skills/{name}/` | Published to marketplace repo |
| Discovery | Only available to you | Discoverable via `/plugin > Discover` |
| Installation | Manual file copy | `/plugin install {name}@claude-plugins-official` |
| Updates | Manual file replacement | `/plugin update {name}` |
| Uninstall | Manual file deletion | `/plugin uninstall {name}` |
| Versioning | None | Semantic versioning via plugin.json |
| Sharing | Share files manually | Users install with one command |

---

## Two Paths to Publishing

### Path A: External Plugin (Community Submission)

For non-Anthropic developers. Your plugin goes into the `external_plugins/` section of the marketplace.

- Submit via: [clau.de/plugin-directory-submission](https://clau.de/plugin-directory-submission)
- Anthropic reviews for quality and security
- Once approved, appears in `/plugin > Discover` for all users
- Your GitHub repo is the source of truth — Anthropic links to it via git SHA

### Path B: Direct GitHub Installation

Users can install directly from your GitHub repo without marketplace approval:

```bash
/plugin install omnireview@github:nexiouscaliver/OmniReview
```

This works immediately but won't appear in the `/plugin > Discover` directory.

### Recommended: Start with Path B, then submit Path A

Get the plugin working and stable via direct GitHub installation first. Once you're confident, submit to the official directory for broader discovery.

---

## Plugin Structure Requirements

Every plugin MUST have:

```
plugin-name/
├── .claude-plugin/
│   └── plugin.json      # Required: Plugin metadata
└── README.md            # Recommended: Documentation
```

A full-featured plugin CAN have:

```
plugin-name/
├── .claude-plugin/
│   └── plugin.json              # Plugin metadata (REQUIRED)
├── .mcp.json                    # MCP server configuration (optional)
├── skills/                      # Skill definitions (optional)
│   ├── model-invoked-skill/
│   │   ├── SKILL.md
│   │   └── references/          # Supporting documents
│   │       └── guide.md
│   └── user-command/
│       └── SKILL.md
├── agents/                      # Agent definitions (optional)
│   └── agent-name.md
├── hooks/                       # Hook configurations (optional)
│   ├── hooks.json
│   └── hook-script.py
├── README.md
├── LICENSE
└── CHANGELOG.md
```

---

## Current vs Target Structure

### Current (Personal Skill)

```
OmniReview/
├── SKILL.md                        # Main skill
├── mr-analyst-prompt.md            # Agent template
├── codebase-reviewer-prompt.md     # Agent template
├── security-reviewer-prompt.md     # Agent template
├── consolidation-guide.md          # Reference doc
├── README.md
├── CONTRIBUTING.md
├── LICENSE
└── PLUGIN_CONVERSION_GUIDE.md     # This file
```

### Target (Plugin Format)

```
OmniReview/
├── .claude-plugin/
│   └── plugin.json                         # NEW: required metadata
├── skills/
│   └── omnireview/
│       ├── SKILL.md                        # MOVED from root
│       └── references/
│           ├── mr-analyst-prompt.md        # MOVED into references/
│           ├── codebase-reviewer-prompt.md # MOVED into references/
│           ├── security-reviewer-prompt.md # MOVED into references/
│           └── consolidation-guide.md      # MOVED into references/
├── README.md                               # KEEP at root
├── CONTRIBUTING.md                         # KEEP at root
├── LICENSE                                 # KEEP at root
├── CHANGELOG.md                            # NEW: version history
└── PLUGIN_CONVERSION_GUIDE.md              # This file (can remove after conversion)
```

### What Changes

1. **Add** `.claude-plugin/plugin.json` — the plugin metadata file
2. **Move** `SKILL.md` into `skills/omnireview/SKILL.md`
3. **Move** all prompt templates into `skills/omnireview/references/`
4. **Update** internal references in SKILL.md from `./mr-analyst-prompt.md` to `./references/mr-analyst-prompt.md`
5. **Add** `CHANGELOG.md` for version tracking

### What Stays the Same

- SKILL.md content and frontmatter (no changes to the skill itself)
- All prompt template contents
- README.md, CONTRIBUTING.md, LICENSE at root

---

## plugin.json Specification

**Location:** `.claude-plugin/plugin.json`

### Minimal (required fields only)

```json
{
  "name": "omnireview",
  "description": "Multi-agent adversarial merge request review — dispatches 3 parallel agents in isolated worktrees for code, security, and process review of GitLab MRs",
  "author": {
    "name": "Shahil Kadia",
    "email": "your-email@example.com"
  }
}
```

### Full (all available fields)

```json
{
  "name": "omnireview",
  "description": "Multi-agent adversarial merge request review — dispatches 3 parallel agents in isolated worktrees for code, security, and process review of GitLab MRs",
  "version": "1.0.0",
  "author": {
    "name": "Shahil Kadia",
    "email": "your-email@example.com"
  },
  "homepage": "https://github.com/nexiouscaliver/OmniReview",
  "repository": "https://github.com/nexiouscaliver/OmniReview",
  "license": "MIT",
  "keywords": [
    "code-review",
    "merge-request",
    "gitlab",
    "security",
    "multi-agent",
    "adversarial-review",
    "owasp"
  ]
}
```

### Field Reference

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique plugin identifier. Lowercase, hyphens, underscores only. |
| `description` | Yes | What the plugin does. Used in discovery and `/plugin > Discover`. |
| `author.name` | Yes | Author display name. |
| `author.email` | Yes | Author contact email. |
| `version` | No | Semantic version string (e.g., "1.0.0"). Used for update detection. |
| `homepage` | No | URL to the plugin's home page or documentation. |
| `repository` | No | URL to the source code repository. |
| `license` | No | License identifier (e.g., "MIT", "Apache-2.0"). |
| `keywords` | No | Array of searchable keywords for discovery. |

---

## Skill File Format

### SKILL.md Frontmatter Reference

OmniReview uses a **model-invoked skill** (Claude auto-activates it based on context). The frontmatter tells Claude when to load the skill.

```yaml
---
name: omnireview
description: Use when reviewing a GitLab merge request, performing code review on an MR, checking MR security, or when given a GitLab MR number or URL to review
---
```

### All Available Frontmatter Fields

| Field | Used By | Description |
|-------|---------|-------------|
| `name` | Both | Skill identifier (lowercase, hyphens) |
| `description` | Both | When to use (model-invoked) or what it does (user-invoked) |
| `version` | Both | Semantic version |
| `argument-hint` | User-invoked | Shows argument format (e.g., `<mr-number>`) |
| `allowed-tools` | User-invoked | Pre-approved tools to reduce permission prompts |
| `model` | Both | Override model: "haiku", "sonnet", "opus" |
| `disable-model-invocation` | User-invoked | `true` = only user can invoke (for side-effect commands) |
| `user-invocable` | Model-invoked | `false` = only Claude can invoke (background knowledge) |
| `license` | Both | License reference |

### Model-Invoked vs User-Invoked

| Type | Triggered By | Example |
|------|-------------|---------|
| Model-invoked | Claude sees matching context and auto-loads the skill | User says "review MR !136" → Claude loads OmniReview |
| User-invoked | User types `/omnireview 136` | Explicit slash command |
| Both (default) | Either trigger works | OmniReview should support both |

OmniReview currently works as model-invoked (no `disable-model-invocation` flag). To also support `/omnireview 136` as a slash command, add `argument-hint`:

```yaml
---
name: omnireview
description: Use when reviewing a GitLab merge request, performing code review on an MR, checking MR security, or when given a GitLab MR number or URL to review
argument-hint: <mr-number>
---
```

---

## Supporting Files Organization

### References Directory

Supporting documents go in `skills/omnireview/references/`:

```
skills/omnireview/
├── SKILL.md
└── references/
    ├── mr-analyst-prompt.md        # MR Analyst agent template
    ├── codebase-reviewer-prompt.md # Codebase Reviewer agent template
    ├── security-reviewer-prompt.md # Security Reviewer agent template
    └── consolidation-guide.md      # Consolidation algorithm and report format
```

Claude Code's three-level loading system:

1. **Metadata** (~100 words) — `name` + `description` from frontmatter. Always in context.
2. **SKILL.md body** — Loaded when the skill triggers. Keep under ~500 lines ideal.
3. **Reference files** — Loaded on demand when the skill references them. No size limit.

This means SKILL.md should be the orchestration document, and heavy content (agent prompts, consolidation algorithm) stays in references/ and is loaded only when needed.

### Updating Internal References

After moving files to `references/`, update paths in SKILL.md:

**Before:**
```markdown
- `./mr-analyst-prompt.md` — MR Analyst (OmniReview)
- `./codebase-reviewer-prompt.md` — Codebase Reviewer (OmniReview)
```

**After:**
```markdown
- `./references/mr-analyst-prompt.md` — MR Analyst (OmniReview)
- `./references/codebase-reviewer-prompt.md` — Codebase Reviewer (OmniReview)
```

---

## Optional Plugin Features

OmniReview currently only uses skills. These are additional plugin features that could be added in the future:

### Agents (`agents/` directory)

Agent definition files that can be spawned as subagents. Each agent gets its own file:

```yaml
---
name: mr-analyst
description: Analyzes MR process quality, commit hygiene, and discussion resolution
tools: Glob, Grep, Read, Bash
model: opus
---

# MR Analyst (OmniReview)
[Agent instructions...]
```

Currently, OmniReview's agent prompts are templates that the main skill fills with context and dispatches via the Agent tool. Converting them to formal agent definitions would allow Claude Code to recognize them as first-class agents.

### Hooks (`hooks/` directory)

Event-driven scripts that run at specific points in Claude's workflow:

```json
{
  "hooks": {
    "PreToolUse": [...],
    "PostToolUse": [...],
    "Stop": [...],
    "UserPromptSubmit": [...]
  }
}
```

Not currently needed for OmniReview, but could be used to:
- Auto-trigger OmniReview when a user mentions an MR number (UserPromptSubmit)
- Validate that worktrees are cleaned up after review (Stop)

### MCP Servers (`.mcp.json`)

External tool integration via Model Context Protocol:

```json
{
  "gitlab-api": {
    "type": "http",
    "url": "https://gitlab.example.com/api"
  }
}
```

Not currently needed — OmniReview uses `glab` CLI directly. Could be useful if we add direct GitLab API integration without requiring glab.

---

## How Claude Code Loads Plugins

### Discovery and Installation

1. User runs `/plugin > Discover` or `/plugin install {name}@{marketplace}`
2. Claude Code reads `marketplace.json` from the marketplace repo
3. Plugin source is cloned/downloaded to `~/.claude/plugins/cache/{marketplace}/{plugin}/`
4. Plugin metadata is registered in `~/.claude/plugins/installed_plugins.json`

### Session Loading

1. At session start, Claude Code reads all installed plugins
2. Skill metadata (name + description) is loaded into context (~100 words each)
3. When a user prompt matches a skill description, the full SKILL.md body is loaded
4. Reference files are loaded on demand as the skill references them

### Key Files on User's Machine

| File | Purpose |
|------|---------|
| `~/.claude/plugins/installed_plugins.json` | Registry of all installed plugins |
| `~/.claude/plugins/known_marketplaces.json` | Available marketplace sources |
| `~/.claude/plugins/blocklist.json` | User-disabled plugins |
| `~/.claude/plugins/cache/{marketplace}/{plugin}/` | Cached plugin files |

---

## Submission Process

### Step 1: Prepare the Plugin

1. Restructure repo to match plugin format (see [Current vs Target Structure](#current-vs-target-structure))
2. Add `.claude-plugin/plugin.json` with metadata
3. Test installation locally
4. Ensure README.md clearly documents what the plugin does and how to use it

### Step 2: Test Locally

Before submitting, verify the plugin works when installed:

```bash
# Method 1: Symlink to plugin cache for testing
mkdir -p ~/.claude/plugins/cache/local-test/omnireview
ln -s /path/to/OmniReview/* ~/.claude/plugins/cache/local-test/omnireview/

# Method 2: Install from local directory (if supported)
# Restart Claude Code and verify the skill appears
```

### Step 3: Submit

**For external plugins (community):**

1. Go to [clau.de/plugin-directory-submission](https://clau.de/plugin-directory-submission)
2. Fill out the submission form with:
   - Plugin name: `omnireview`
   - GitHub repository URL: `https://github.com/nexiouscaliver/OmniReview`
   - Description of what it does
   - Any special requirements (glab CLI)
3. Anthropic reviews for quality and security
4. Once approved, your repo appears in `external_plugins/` in the marketplace

**What Anthropic looks for:**
- Clean plugin structure with valid plugin.json
- Clear documentation (README)
- No security concerns (no malicious hooks, no data exfiltration)
- Useful functionality that benefits Claude Code users
- Proper license

### Step 4: After Approval

Your plugin entry in `marketplace.json` would look like:

```json
{
  "name": "omnireview",
  "description": "Multi-agent adversarial merge request review — 3 parallel agents, 3 worktrees, 1 consolidated report for GitLab MRs",
  "source": {
    "source": "url",
    "url": "https://github.com/nexiouscaliver/OmniReview.git",
    "sha": "commit-sha-at-approval-time"
  },
  "homepage": "https://github.com/nexiouscaliver/OmniReview"
}
```

Users can then install with:
```
/plugin install omnireview@claude-plugins-official
```

---

## Installation by End Users

Once published, users install OmniReview with:

```bash
# From official marketplace (after approval)
/plugin install omnireview@claude-plugins-official

# From GitHub directly (works immediately)
/plugin install omnireview@github:nexiouscaliver/OmniReview

# Update to latest version
/plugin update omnireview

# Uninstall
/plugin uninstall omnireview
```

After installation, restart Claude Code session, then:
```
/omnireview 136
```

or just ask:
```
Review MR !136
```

---

## Reference: Official Example Plugin

The official reference implementation at `plugins/example-plugin/` in the marketplace repo demonstrates all extension points:

```
example-plugin/
├── .claude-plugin/
│   └── plugin.json            # {"name": "example-plugin", "description": "...", "author": {...}}
├── .mcp.json                  # MCP server configuration example
├── skills/
│   ├── example-skill/
│   │   └── SKILL.md           # Model-invoked skill (contextual guidance)
│   └── example-command/
│       └── SKILL.md           # User-invoked skill (slash command)
├── commands/
│   └── example-command.md     # Legacy format (deprecated, use skills/ instead)
├── LICENSE
└── README.md
```

Key takeaways from the example:
- `skills/` directory is the preferred format for both model-invoked and user-invoked skills
- `commands/` is legacy and loaded identically — use `skills/` for new plugins
- plugin.json only needs name, description, and author
- MCP servers are optional and configured in `.mcp.json` at root

---

## Reference: Marketplace Registry Format

The marketplace is a git repository with this structure:

```
claude-plugins-official/
├── .claude-plugin/
│   └── marketplace.json       # Registry of all available plugins
├── plugins/                   # Anthropic-maintained plugins
│   ├── code-review/
│   ├── feature-dev/
│   ├── pr-review-toolkit/
│   └── ...
└── external_plugins/          # Community-submitted plugins
    ├── context7/
    ├── gitlab/
    ├── playwright/
    ├── serena/
    └── ...
```

Each entry in `marketplace.json` specifies either:
- `"source": "./plugins/plugin-name"` — for internal plugins (in-repo)
- `"source": {"source": "url", "url": "https://github.com/user/repo.git", "sha": "..."}` — for external plugins (GitHub link)

---

## Reference: Existing Plugins in Directory

### Internal Plugins (by Anthropic)

code-review, commit-commands, claude-code-setup, claude-md-management, code-simplifier, example-plugin, explanatory-output-style, feature-dev, frontend-design, hookify, learning-output-style, math-olympiad, mcp-server-dev, playground, plugin-dev, pr-review-toolkit, ralph-loop, security-guidance, skill-creator, agent-sdk-dev, and various LSP plugins.

### External Plugins (Community)

context7, discord, firebase, github, gitlab, greptile, imessage, laravel-boost, linear, playwright, serena, slack, supabase, telegram, asana, fakechat, and more.

OmniReview would join the external plugins list upon approval.

---

## Conversion Checklist

Use this checklist when ready to convert:

### Structure
- [ ] Create `.claude-plugin/` directory
- [ ] Create `.claude-plugin/plugin.json` with name, description, author
- [ ] Create `skills/omnireview/` directory
- [ ] Move `SKILL.md` to `skills/omnireview/SKILL.md`
- [ ] Create `skills/omnireview/references/` directory
- [ ] Move `mr-analyst-prompt.md` to `skills/omnireview/references/`
- [ ] Move `codebase-reviewer-prompt.md` to `skills/omnireview/references/`
- [ ] Move `security-reviewer-prompt.md` to `skills/omnireview/references/`
- [ ] Move `consolidation-guide.md` to `skills/omnireview/references/`
- [ ] Update internal references in SKILL.md (`./` → `./references/`)
- [ ] Add `argument-hint: <mr-number>` to SKILL.md frontmatter for slash command support
- [ ] Create `CHANGELOG.md`

### Quality
- [ ] README.md clearly explains what the plugin does
- [ ] LICENSE file present (MIT)
- [ ] No hardcoded paths or user-specific data in skill files
- [ ] All glab commands work for any GitLab instance (not just yours)
- [ ] Plugin tested via local installation

### Testing
- [ ] Install plugin locally and restart Claude Code
- [ ] Verify `/omnireview 136` works as a slash command
- [ ] Verify "review MR !136" triggers the skill automatically
- [ ] Verify all 7 phases execute correctly
- [ ] Verify worktree cleanup happens even on failure

### Submission
- [ ] Push final plugin structure to GitHub
- [ ] Submit via [clau.de/plugin-directory-submission](https://clau.de/plugin-directory-submission)
- [ ] Wait for Anthropic review
- [ ] After approval, verify installation via `/plugin install omnireview@claude-plugins-official`

---

*This document was created on 2026-03-26. Update it as the Claude Code plugin ecosystem evolves.*
