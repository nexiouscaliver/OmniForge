# Converting OmniReview to an Official Claude Code Plugin

This document captures everything needed to convert OmniReview from a personal Claude Code skill into an official Anthropic plugin listed in the [claude-plugins-official](https://github.com/anthropics/claude-plugins-official) directory. It also serves as a comprehensive reference for all Claude Code plugin extension points — skills, agents, hooks, MCP servers — so that future expansions of OmniReview have a complete knowledge base.

---

## Table of Contents

- [Overview](#overview)
- [Two Paths to Publishing](#two-paths-to-publishing)
- [Plugin Structure Requirements](#plugin-structure-requirements)
- [Current vs Target Structure](#current-vs-target-structure)
- [plugin.json — Complete Specification](#pluginjson--complete-specification)
- [Skills — Complete Reference](#skills--complete-reference)
- [Agents — Complete Reference](#agents--complete-reference)
- [Hooks — Complete Reference](#hooks--complete-reference)
- [MCP Servers — Complete Reference](#mcp-servers--complete-reference)
- [How Claude Code Loads Plugins](#how-claude-code-loads-plugins)
- [Submission Process](#submission-process)
- [Installation by End Users](#installation-by-end-users)
- [Reference: Official Examples](#reference-official-examples)
- [Reference: Marketplace Registry Format](#reference-marketplace-registry-format)
- [Reference: Existing Plugins in Directory](#reference-existing-plugins-in-directory)
- [Future Expansion Ideas for OmniReview](#future-expansion-ideas-for-omnireview)
- [Conversion Checklist](#conversion-checklist)

---

## Overview

Claude Code supports a plugin system that allows skills, commands, agents, hooks, and MCP servers to be packaged, distributed, and installed by any Claude Code user. Plugins are published to the [claude-plugins-official](https://github.com/anthropics/claude-plugins-official) marketplace maintained by Anthropic.

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

Get the plugin working and stable via direct GitHub installation first. Once confident, submit to the official directory for broader discovery.

---

## Plugin Structure Requirements

### Minimal Plugin (just metadata)

```
plugin-name/
├── .claude-plugin/
│   └── plugin.json      # REQUIRED: Plugin metadata
└── README.md            # Recommended
```

### Full-Featured Plugin (all extension points)

```
plugin-name/
├── .claude-plugin/
│   └── plugin.json              # REQUIRED: Plugin metadata
│
├── skills/                      # Skills (model-invoked + user-invoked)
│   ├── model-skill/
│   │   ├── SKILL.md             # Main skill definition
│   │   ├── references/          # Supporting documents loaded on demand
│   │   │   ├── guide.md
│   │   │   └── templates.md
│   │   ├── examples/            # Example files
│   │   │   └── sample.md
│   │   └── scripts/             # Helper scripts (executed, not loaded)
│   │       └── helper.sh
│   └── slash-command/
│       └── SKILL.md             # User-invoked slash command
│
├── agents/                      # Agent definitions (spawnable subagents)
│   ├── agent-one.md
│   └── agent-two.md
│
├── hooks/                       # Event-driven hooks
│   ├── hooks.json               # Hook configuration (which events, which scripts)
│   ├── pretooluse.py            # PreToolUse hook script
│   ├── posttooluse.py           # PostToolUse hook script
│   ├── stop.py                  # Stop hook script
│   └── session-start.sh         # SessionStart hook script
│
├── .mcp.json                    # MCP server configuration
│
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
│   └── plugin.json                         # NEW
├── skills/
│   └── omnireview/
│       ├── SKILL.md                        # MOVED from root
│       └── references/
│           ├── mr-analyst-prompt.md        # MOVED
│           ├── codebase-reviewer-prompt.md # MOVED
│           ├── security-reviewer-prompt.md # MOVED
│           └── consolidation-guide.md      # MOVED
├── README.md
├── CONTRIBUTING.md
├── LICENSE
├── CHANGELOG.md                            # NEW
└── PLUGIN_CONVERSION_GUIDE.md
```

### What Changes

1. **Add** `.claude-plugin/plugin.json`
2. **Move** `SKILL.md` into `skills/omnireview/SKILL.md`
3. **Move** all prompt templates into `skills/omnireview/references/`
4. **Update** internal references in SKILL.md from `./mr-analyst-prompt.md` to `./references/mr-analyst-prompt.md`
5. **Add** `argument-hint: <mr-number>` to SKILL.md frontmatter for slash command support
6. **Add** `CHANGELOG.md`

---

## plugin.json — Complete Specification

**Location:** `.claude-plugin/plugin.json`

This is the ONLY required file for a plugin. Without it, Claude Code won't recognize the directory as a plugin.

### OmniReview plugin.json

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

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | Yes | string | Unique plugin identifier. Lowercase, hyphens, underscores only. Must match the repo/directory name. |
| `description` | Yes | string | What the plugin does. Shown in `/plugin > Discover` and plugin listings. Be specific and searchable. |
| `author.name` | Yes | string | Author display name. |
| `author.email` | Yes | string | Author contact email. |
| `version` | No | string | Semantic version (e.g., "1.0.0", "2.3.1"). Used by Claude Code to detect available updates. |
| `homepage` | No | string (URL) | Plugin's home page or documentation URL. Shown to users in plugin info. |
| `repository` | No | string (URL) | Source code repository URL. |
| `license` | No | string | SPDX license identifier (e.g., "MIT", "Apache-2.0", "GPL-3.0"). |
| `keywords` | No | string[] | Array of searchable keywords. Helps users find your plugin in Discover. |

### Real-World Examples from Official Plugins

**Minimal (example-plugin):**
```json
{
  "name": "example-plugin",
  "description": "A comprehensive example plugin demonstrating all Claude Code extension options including commands, agents, skills, hooks, and MCP servers",
  "author": {
    "name": "Anthropic",
    "email": "support@anthropic.com"
  }
}
```

**External plugin (gitlab):**
```json
{
  "name": "gitlab",
  "description": "GitLab MCP server integration for Claude Code",
  "author": {
    "name": "GitLab",
    "email": "support@gitlab.com"
  }
}
```

---

## Skills — Complete Reference

Skills are the primary way plugins extend Claude Code. They provide instructions, workflows, and contextual guidance that Claude follows.

### Directory Structure

```
skills/
├── model-invoked-skill/       # Claude auto-activates based on context
│   ├── SKILL.md               # Main definition (required)
│   ├── references/            # Heavy docs loaded on demand (optional)
│   │   ├── api-reference.md
│   │   └── patterns.md
│   ├── examples/              # Examples loaded on demand (optional)
│   │   └── sample.md
│   └── scripts/               # Executable scripts (optional)
│       └── helper.sh
│
└── user-invoked-command/      # User types /command-name
    └── SKILL.md
```

### SKILL.md Frontmatter — All Fields

```yaml
---
# REQUIRED FIELDS
name: skill-name                    # Identifier (lowercase, hyphens only)
description: When to use this skill # Trigger conditions or /help text

# OPTIONAL FIELDS
version: 1.0.0                      # Semantic version
argument-hint: <arg1> [optional]    # Shown to user for slash commands
allowed-tools: [Read, Glob, Grep]   # Pre-approved tools (skips permission prompts)
model: sonnet                       # Force specific model: haiku | sonnet | opus
disable-model-invocation: true      # true = user-only (/command), Claude cannot auto-invoke
user-invocable: false               # false = Claude-only, user cannot /invoke
license: MIT                        # License reference
---
```

### Three Types of Skills

#### 1. Model-Invoked (Claude auto-triggers)

Claude reads the `description` and decides whether to load the skill based on the user's request.

```yaml
---
name: omnireview
description: Use when reviewing a GitLab merge request, performing code review on an MR, checking MR security, or when given a GitLab MR number or URL to review
---
```

**When to use:** Skills that should activate based on what the user is asking about.

**Description writing tips:**
- Start with "Use when..." or "This skill should be used when..."
- Include specific phrases users might say: "review MR", "check this merge request"
- Include keywords: "GitLab", "merge request", "code review", "security"
- Do NOT summarize the workflow in the description — only list triggers

#### 2. User-Invoked (Slash command)

User explicitly types `/command-name args`. Shows up in `/help`.

```yaml
---
name: omnireview
description: Review a GitLab merge request with 3 parallel agents
argument-hint: <mr-number>
allowed-tools: [Read, Glob, Grep, Bash, Agent, Write, Edit]
---
```

**`argument-hint`** — Shown to the user when they type `/omnireview`. Examples:
- `<mr-number>` — single required argument
- `<mr-number> [--deep]` — required + optional flag
- `<url-or-number>` — flexible input

**`allowed-tools`** — Pre-approves these tools so the user isn't prompted for permission:
- `Read`, `Glob`, `Grep` — file reading
- `Bash` — shell commands (use carefully — broad permission)
- `Agent` — spawning subagents
- `Write`, `Edit` — file modification
- `WebFetch`, `WebSearch` — web access
- `TodoWrite` — task management

#### 3. Both (Default — Recommended for OmniReview)

Omit both `disable-model-invocation` and `user-invocable`. Claude can auto-trigger it AND users can `/invoke` it.

```yaml
---
name: omnireview
description: Use when reviewing a GitLab merge request, performing code review on an MR, checking MR security, or when given a GitLab MR number or URL to review
argument-hint: <mr-number>
allowed-tools: [Read, Glob, Grep, Bash, Agent]
---
```

### Three-Level Loading System

Claude Code loads skills progressively to conserve context:

| Level | What's Loaded | When | Size Target |
|-------|--------------|------|-------------|
| 1. Metadata | `name` + `description` from frontmatter | Always in context (every session) | ~100 words max |
| 2. SKILL.md body | Full skill content below frontmatter | When skill triggers (context match or /command) | ~500 lines ideal |
| 3. Reference files | Files in `references/`, `examples/`, `scripts/` | On demand when SKILL.md references them | No limit |

**Implication for OmniReview:** Keep SKILL.md as the orchestration document. Agent prompt templates (heavy content) stay in `references/` and are loaded only when Phase 3 dispatches agents.

### Supporting File Types

| Directory | Purpose | How Loaded |
|-----------|---------|------------|
| `references/` | Documentation, templates, guides | Loaded into context when referenced |
| `examples/` | Example files, sample configs | Loaded into context when referenced |
| `scripts/` | Executable helper scripts | Executed via Bash, NOT loaded into context |

---

## Agents — Complete Reference

Agents are spawnable subagents defined in their own files. When defined in a plugin, they appear as available agent types in the Agent tool's `subagent_type` parameter.

### Directory Structure

```
agents/
├── mr-analyst.md
├── codebase-reviewer.md
└── security-reviewer.md
```

### Agent File Format

```yaml
---
name: agent-name
description: What this agent specializes in (used to select the right agent for a task)
tools: Glob, Grep, LS, Read, Bash, WebFetch, TodoWrite, WebSearch
model: opus
color: green
---

# Agent Title

You are a [role description].

## Core Responsibilities
- Responsibility 1
- Responsibility 2

## Process
[Step-by-step instructions]

## Output Format
[What this agent should return]
```

### Agent Frontmatter — All Fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | Yes | string | Agent identifier (lowercase, hyphens). Used in `subagent_type` parameter. |
| `description` | Yes | string | What the agent does. Claude reads this to decide which agent to spawn. |
| `tools` | No | string (comma-separated) | Tools available to this agent. Restricts what the agent can do. |
| `model` | No | string | Claude model: `haiku` (fast/cheap), `sonnet` (balanced), `opus` (most capable). |
| `color` | No | string | UI color for the agent's output. Options: `green`, `blue`, `red`, `yellow`, etc. |

### Available Tools for Agents

```
Glob, Grep, LS, Read, Write, Edit, MultiEdit,
Bash, BashOutput, KillShell,
NotebookRead, NotebookEdit,
WebFetch, WebSearch,
TodoWrite,
Agent (can spawn sub-sub-agents)
```

### Real-World Example: feature-dev/code-architect

```yaml
---
name: code-architect
description: Designs feature architectures by analyzing existing codebase patterns and conventions, then providing comprehensive implementation blueprints with specific files to create/modify, component designs, data flows, and build sequences
tools: Glob, Grep, LS, Read, NotebookRead, WebFetch, TodoWrite, WebSearch, KillShell, BashOutput
model: sonnet
color: green
---

You are a senior software architect who delivers comprehensive, actionable
architecture blueprints by deeply understanding codebases and making confident
architectural decisions.

## Core Process

**1. Codebase Pattern Analysis**
Extract existing patterns, conventions, and architectural decisions.

**2. Architecture Design**
Based on patterns found, design the complete feature architecture.

**3. Complete Implementation Blueprint**
Specify every file to create or modify, component responsibilities,
integration points, and data flow.

## Output Guidance

Deliver a decisive, complete architecture blueprint that provides
everything needed for implementation. Include:

- Patterns & Conventions Found
- Architecture Decision
- Component Design
- Implementation Map
- Data Flow
- Build Sequence
- Critical Details
```

### How Agents Differ from Skill-Dispatched Subagents

| Aspect | Plugin Agent | Skill-Dispatched Subagent |
|--------|-------------|--------------------------|
| Definition | `agents/name.md` file | Prompt template filled at runtime |
| Discovery | Claude sees it as available agent type | Only used when skill explicitly dispatches |
| Tools | Defined in frontmatter (fixed) | Inherits from dispatch context |
| Model | Defined in frontmatter | Specified in Agent tool call |
| Context | Gets task description only | Gets full injected context package |

**OmniReview currently uses skill-dispatched subagents** (prompt templates filled with MR data). Converting to formal agents would make them available as reusable agent types across all skills, not just OmniReview.

---

## Hooks — Complete Reference

Hooks are event-driven scripts that run at specific points in Claude's workflow. They can block actions, modify behavior, or add context.

### Directory Structure

```
hooks/
├── hooks.json              # Configuration: which events trigger which scripts
├── pretooluse.py           # Script for PreToolUse event
├── posttooluse.py          # Script for PostToolUse event
├── stop.py                 # Script for Stop event
├── session-start.sh        # Script for SessionStart event
└── userpromptsubmit.py     # Script for UserPromptSubmit event
```

### hooks.json — Complete Specification

```json
{
  "description": "Human-readable description of what these hooks do",
  "hooks": {
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pretooluse.py",
            "timeout": 10
          }
        ],
        "matcher": "Edit|Write|MultiEdit"
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/posttooluse.py",
            "timeout": 10
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/stop.py",
            "timeout": 10
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/userpromptsubmit.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

### Hook Events — All Types

| Event | When It Fires | Can Block? | Use Cases |
|-------|--------------|------------|-----------|
| `PreToolUse` | Before Claude executes any tool | Yes | Validate commands, block dangerous operations, add warnings |
| `PostToolUse` | After a tool finishes executing | No | Validate outputs, log actions, format results |
| `Stop` | When Claude is about to stop responding | Yes | Check if requirements are met, ensure cleanup happened |
| `SessionStart` | When a new Claude Code session begins | No | Inject context, set up environment, load state |
| `UserPromptSubmit` | When the user submits a message | Yes | Validate input, auto-trigger skills, modify context |

### Hook Configuration Fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `type` | Yes | string | Always `"command"` — run a shell command |
| `command` | Yes | string | Shell command to execute. Use `${CLAUDE_PLUGIN_ROOT}` for plugin root path. |
| `timeout` | No | number | Seconds before timeout. Default varies. Typically 10. |
| `matcher` | No | string | Pipe-separated tool names to filter (e.g., `"Edit\|Write\|MultiEdit"`). Only for PreToolUse/PostToolUse. |

### Environment Variables in Hooks

| Variable | Available In | Description |
|----------|-------------|-------------|
| `${CLAUDE_PLUGIN_ROOT}` | All hooks | Absolute path to the plugin's root directory |
| `stdin` (JSON) | PreToolUse, PostToolUse | JSON with tool name, parameters, and result |

### Hook Script I/O Protocol

**Input:** Hook scripts receive context via stdin as JSON.

**Output:** Hook scripts communicate back via stdout:

- **Empty stdout** → Hook passes, no message
- **Text on stdout** → Message shown to Claude (for PreToolUse: shown as warning/context)
- **Exit code 0** → Hook passes
- **Exit code non-0** → Hook blocks the action (PreToolUse only)

### Real-World Example: Security Reminder Hook

From the `security-guidance` plugin — warns when editing files that might contain security-sensitive patterns:

**hooks.json:**
```json
{
  "description": "Security reminder hook that warns about potential security issues when editing files",
  "hooks": {
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/security_reminder_hook.py"
          }
        ],
        "matcher": "Edit|Write|MultiEdit"
      }
    ]
  }
}
```

**security_reminder_hook.py (simplified):**
```python
#!/usr/bin/env python3
import json
import sys

# Read tool use context from stdin
input_data = json.loads(sys.stdin.read())
tool_name = input_data.get("tool_name", "")
file_path = input_data.get("tool_input", {}).get("file_path", "")

# Check if the file being edited matches security patterns
SECURITY_PATTERNS = [
    {
        "path_check": lambda p: ".github/workflows/" in p,
        "reminder": "You are editing a GitHub Actions workflow. Watch for command injection..."
    },
    {
        "path_check": lambda p: "Dockerfile" in p,
        "reminder": "You are editing a Dockerfile. Avoid running as root..."
    },
]

for pattern in SECURITY_PATTERNS:
    if pattern["path_check"](file_path):
        # Print warning — Claude will see this as context
        print(f"Warning: {pattern['reminder']}")
        sys.exit(0)  # Exit 0 = allow the edit (just warn)

# No matches, allow silently
sys.exit(0)
```

### Real-World Example: Session Start Hook

From the `learning-output-style` plugin — injects learning mode instructions at session start:

**hooks.json:**
```json
{
  "description": "Learning mode hook that adds interactive learning instructions",
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks-handlers/session-start.sh"
          }
        ]
      }
    ]
  }
}
```

**session-start.sh:**
```bash
#!/bin/bash
# Output text that gets injected into Claude's context at session start
echo "You are in 'learning' output style mode. Instead of implementing everything yourself..."
```

---

## MCP Servers — Complete Reference

MCP (Model Context Protocol) servers provide Claude with external tool integrations — APIs, databases, services — that appear as callable tools.

### File Location

`.mcp.json` at the plugin root.

### Configuration Format

```json
{
  "server-name": {
    "type": "http|command",
    ...server-specific-fields
  }
}
```

### Server Types

#### HTTP Server (Remote API)

Connects to a remote MCP endpoint over HTTPS.

```json
{
  "gitlab": {
    "type": "http",
    "url": "https://gitlab.com/api/v4/mcp"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | `"http"` |
| `url` | Yes | Full HTTPS URL of the MCP endpoint |

#### Command Server (Local Process)

Spawns a local process that communicates via stdio.

```json
{
  "context7": {
    "command": "npx",
    "args": ["-y", "@upstash/context7-mcp"]
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `command` | Yes | Executable to run (e.g., `"npx"`, `"python3"`, `"node"`) |
| `args` | No | Array of command-line arguments |
| `env` | No | Object of environment variables to set |

### Real-World Examples

**GitLab MCP (HTTP):**
```json
{
  "gitlab": {
    "type": "http",
    "url": "https://gitlab.com/api/v4/mcp"
  }
}
```

**Context7 (Command — npx):**
```json
{
  "context7": {
    "command": "npx",
    "args": ["-y", "@upstash/context7-mcp"]
  }
}
```

**Multiple servers:**
```json
{
  "gitlab-api": {
    "type": "http",
    "url": "https://gitlab.com/api/v4/mcp"
  },
  "local-analyzer": {
    "command": "python3",
    "args": ["./scripts/analyzer-server.py"],
    "env": {
      "ANALYSIS_MODE": "deep"
    }
  }
}
```

### How MCP Tools Appear to Claude

Once configured, MCP server tools appear as callable tools with the prefix `mcp__{server-name}__{tool-name}`. For example, a GitLab MCP server might expose:

- `mcp__gitlab__list_merge_requests`
- `mcp__gitlab__get_merge_request`
- `mcp__gitlab__create_note`

---

## How Claude Code Loads Plugins

### Discovery and Installation Flow

```
User: /plugin install omnireview@claude-plugins-official
  │
  ├── Claude Code reads marketplace.json from the marketplace repo
  ├── Finds "omnireview" entry with source URL/path
  ├── Clones/downloads to ~/.claude/plugins/cache/{marketplace}/omnireview/
  ├── Reads .claude-plugin/plugin.json for metadata
  ├── Registers in ~/.claude/plugins/installed_plugins.json
  └── Ready on next session restart
```

### Session Loading Flow

```
Session starts
  │
  ├── Read all installed plugins from installed_plugins.json
  ├── For each plugin:
  │   ├── Load skill metadata (name + description) into context (~100 words each)
  │   ├── Register hooks (hooks.json → event listeners)
  │   ├── Start MCP servers (.mcp.json → background processes)
  │   └── Register agents (agents/*.md → available agent types)
  │
  ├── User sends message
  │   ├── Claude evaluates all skill descriptions against user's request
  │   ├── Matching skills: full SKILL.md body loaded into context
  │   ├── Skill references files → loaded on demand
  │   └── Skill dispatches agents → agent definitions loaded
  │
  └── Hooks fire at their respective events throughout the session
```

### Key Files on User's Machine

| File | Purpose |
|------|---------|
| `~/.claude/plugins/installed_plugins.json` | Registry of all installed plugins (name, version, path, install date) |
| `~/.claude/plugins/known_marketplaces.json` | Available marketplace sources and their locations |
| `~/.claude/plugins/blocklist.json` | User-disabled plugins (plugin ID, reason, date) |
| `~/.claude/plugins/cache/{marketplace}/{plugin}/` | Cached plugin files (the actual code) |

### installed_plugins.json Format

```json
{
  "omnireview@claude-plugins-official": [
    {
      "scope": "user",
      "installPath": "~/.claude/plugins/cache/claude-plugins-official/omnireview/abc123",
      "version": "1.0.0",
      "installedAt": "2026-03-26T00:00:00.000Z",
      "lastUpdated": "2026-03-26T00:00:00.000Z",
      "gitCommitSha": "abc123def456"
    }
  ]
}
```

---

## Submission Process

### Step 1: Prepare the Plugin

1. Restructure repo to match plugin format (see [Current vs Target Structure](#current-vs-target-structure))
2. Add `.claude-plugin/plugin.json` with metadata
3. Test installation locally
4. Ensure README.md clearly documents what the plugin does

### Step 2: Test Locally

```bash
# Symlink to plugin cache for testing
mkdir -p ~/.claude/plugins/cache/local-test/omnireview
ln -s /path/to/OmniReview/* ~/.claude/plugins/cache/local-test/omnireview/

# Restart Claude Code and verify skill appears
# Test /omnireview 136
# Test natural language: "review MR !136"
```

### Step 3: Submit

1. Go to [clau.de/plugin-directory-submission](https://clau.de/plugin-directory-submission)
2. Fill out: plugin name, GitHub URL, description, requirements
3. Anthropic reviews for quality and security
4. Once approved, appears in `external_plugins/` in marketplace

**What Anthropic reviews:**
- Valid `.claude-plugin/plugin.json`
- Clear README documentation
- No security concerns (malicious hooks, data exfiltration)
- Useful functionality for Claude Code users
- Proper license file

### Step 4: After Approval

Your entry in `marketplace.json`:

```json
{
  "name": "omnireview",
  "description": "Multi-agent adversarial merge request review — 3 parallel agents, 3 worktrees, 1 consolidated report",
  "source": {
    "source": "url",
    "url": "https://github.com/nexiouscaliver/OmniReview.git",
    "sha": "commit-sha-at-approval-time"
  },
  "homepage": "https://github.com/nexiouscaliver/OmniReview"
}
```

---

## Installation by End Users

```bash
# From official marketplace (after approval)
/plugin install omnireview@claude-plugins-official

# From GitHub directly (works immediately, no approval needed)
/plugin install omnireview@github:nexiouscaliver/OmniReview

# Update
/plugin update omnireview

# Uninstall
/plugin uninstall omnireview
```

After installation, **restart Claude Code session**, then:
```
/omnireview 136
```

---

## Reference: Official Examples

### example-plugin (Anthropic's reference implementation)

```
example-plugin/
├── .claude-plugin/
│   └── plugin.json            # Minimal: name, description, author
├── .mcp.json                  # HTTP MCP example
├── skills/
│   ├── example-skill/
│   │   └── SKILL.md           # Model-invoked (auto-triggered by context)
│   └── example-command/
│       └── SKILL.md           # User-invoked (slash command)
├── commands/
│   └── example-command.md     # Legacy format (deprecated — use skills/)
├── LICENSE
└── README.md
```

### feature-dev (Multi-agent plugin)

```
feature-dev/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   └── feature-dev/
│       └── SKILL.md
├── agents/
│   ├── code-architect.md      # Architecture design agent
│   ├── code-explorer.md       # Codebase analysis agent
│   └── code-reviewer.md       # Code review agent
└── README.md
```

### security-guidance (Hook-based plugin)

```
security-guidance/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json             # PreToolUse on Edit|Write|MultiEdit
│   └── security_reminder_hook.py
└── README.md
```

### gitlab (MCP-only external plugin)

```
gitlab/
├── .claude-plugin/
│   └── plugin.json
└── .mcp.json                  # {"gitlab": {"type": "http", "url": "..."}}
```

---

## Reference: Marketplace Registry Format

```
claude-plugins-official/
├── .claude-plugin/
│   └── marketplace.json       # Registry of ALL available plugins
├── plugins/                   # Anthropic-maintained (internal)
│   ├── code-review/
│   ├── feature-dev/
│   ├── pr-review-toolkit/
│   └── ... (32 plugins)
└── external_plugins/          # Community-submitted
    ├── context7/
    ├── gitlab/
    ├── playwright/
    ├── serena/
    └── ... (16+ plugins)
```

### marketplace.json Entry Formats

**Internal plugin (in-repo):**
```json
{
  "name": "feature-dev",
  "description": "Guided feature development...",
  "author": {"name": "Anthropic", "email": "support@anthropic.com"},
  "source": "./plugins/feature-dev",
  "category": "development"
}
```

**External plugin (GitHub URL):**
```json
{
  "name": "omnireview",
  "description": "Multi-agent adversarial MR review...",
  "source": {
    "source": "url",
    "url": "https://github.com/nexiouscaliver/OmniReview.git",
    "sha": "abc123"
  },
  "homepage": "https://github.com/nexiouscaliver/OmniReview"
}
```

**External plugin (git subdirectory):**
```json
{
  "name": "some-plugin",
  "source": {
    "source": "git-subdir",
    "url": "org/repo",
    "path": "plugins/some-plugin",
    "ref": "main",
    "sha": "abc123"
  }
}
```

---

## Reference: Existing Plugins in Directory

### Internal Plugins (by Anthropic) — 32 plugins

agent-sdk-dev, clangd-lsp, claude-code-setup, claude-md-management, code-review, code-simplifier, commit-commands, csharp-lsp, example-plugin, explanatory-output-style, feature-dev, frontend-design, gopls-lsp, hookify, jdtls-lsp, kotlin-lsp, learning-output-style, lua-lsp, math-olympiad, mcp-server-dev, php-lsp, playground, plugin-dev, pr-review-toolkit, pyright-lsp, ralph-loop, ruby-lsp, rust-analyzer-lsp, security-guidance, skill-creator, swift-lsp, typescript-lsp.

### External Plugins (Community) — 16+ plugins

asana, context7, discord, fakechat, firebase, github, gitlab, greptile, imessage, laravel-boost, linear, playwright, serena, slack, supabase, telegram.

OmniReview would join the external plugins list upon approval.

---

## Future Expansion Ideas for OmniReview

These are features that could be added using the extension points documented above. Each section references the relevant plugin capability.

### Convert Prompt Templates to Formal Agents

**Extension point:** `agents/` directory

Currently, OmniReview dispatches subagents by filling prompt templates at runtime. Converting to formal agent definitions would:
- Make agents reusable across skills (not just OmniReview)
- Allow model/tool constraints in frontmatter
- Give agents their own identity in Claude Code's agent system

```
agents/
├── omni-mr-analyst.md          # MR process review agent
├── omni-codebase-reviewer.md   # Deep code review agent
└── omni-security-reviewer.md   # Security audit agent
```

### Add Worktree Cleanup Hook

**Extension point:** `hooks/` with `Stop` event

Ensure worktrees are cleaned up even if the user quits mid-review:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/cleanup-worktrees.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

### Add MR Detection Hook

**Extension point:** `hooks/` with `UserPromptSubmit` event

Auto-detect when the user mentions an MR number and suggest running OmniReview:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/detect-mr-mention.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

### Add GitLab MCP Server

**Extension point:** `.mcp.json`

Replace `glab` CLI dependency with direct GitLab API integration:

```json
{
  "gitlab-review": {
    "type": "http",
    "url": "https://gitlab.com/api/v4/mcp"
  }
}
```

This would make OmniReview work without requiring `glab` to be installed.

### Add GitHub PR Support

**Extension point:** Additional skill in `skills/`

```
skills/
├── omnireview/            # GitLab MR review (existing)
│   └── SKILL.md
└── omnireview-github/     # GitHub PR review (new)
    └── SKILL.md
```

Or: single skill that auto-detects whether the repo uses GitHub or GitLab and adjusts commands accordingly.

### Add Custom Review Checklists

**Extension point:** `skills/omnireview/references/`

Allow teams to add project-specific checklists:

```
skills/omnireview/references/
├── checklists/
│   ├── default.md              # Default review checklist
│   ├── frontend-react.md       # React-specific checks
│   ├── backend-python.md       # Python/FastAPI checks
│   └── infrastructure.md       # CI/CD and infra checks
```

The skill would detect the project type and load the appropriate checklist.

---

## Conversion Checklist

Use this when ready to convert from personal skill to plugin:

### Structure
- [ ] Create `.claude-plugin/` directory at repo root
- [ ] Create `.claude-plugin/plugin.json` with name, description, author, version
- [ ] Create `skills/omnireview/` directory
- [ ] Move `SKILL.md` to `skills/omnireview/SKILL.md`
- [ ] Create `skills/omnireview/references/` directory
- [ ] Move `mr-analyst-prompt.md` to `skills/omnireview/references/`
- [ ] Move `codebase-reviewer-prompt.md` to `skills/omnireview/references/`
- [ ] Move `security-reviewer-prompt.md` to `skills/omnireview/references/`
- [ ] Move `consolidation-guide.md` to `skills/omnireview/references/`
- [ ] Update all internal references in SKILL.md (`./` to `./references/`)
- [ ] Add `argument-hint: <mr-number>` to SKILL.md frontmatter
- [ ] Add `allowed-tools` to SKILL.md frontmatter
- [ ] Create `CHANGELOG.md` with version 1.0.0

### Quality
- [ ] README.md clearly explains what the plugin does
- [ ] LICENSE file present (MIT)
- [ ] No hardcoded paths or user-specific data in any file
- [ ] All glab commands work for any GitLab instance
- [ ] Plugin tested via local installation

### Testing
- [ ] Install plugin locally (symlink to cache) and restart Claude Code
- [ ] Verify `/omnireview 136` works as a slash command
- [ ] Verify "review MR !136" triggers the skill automatically
- [ ] Verify all 7 phases execute correctly
- [ ] Verify worktree cleanup happens even on failure
- [ ] Verify on a fresh machine / clean Claude Code install

### Submission
- [ ] Push final plugin structure to GitHub
- [ ] Submit via [clau.de/plugin-directory-submission](https://clau.de/plugin-directory-submission)
- [ ] Wait for Anthropic review
- [ ] After approval, verify: `/plugin install omnireview@claude-plugins-official`
- [ ] Update README with official install command

---

*This document was created on 2026-03-26. Update it as the Claude Code plugin ecosystem evolves.*
*Source: [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official)*
