---
name: gitlab-mr
description: This skill should be used when the user asks to "create MR", "create merge request", "glab mr", "create GitLab MR", "open merge request", "submit MR", or needs to create a GitLab merge request using the glab CLI. Automates MR creation with auto-populated title/description from commits, branch management, and optional reviewers/assignees.
version: 1.2.0
license: Apache-2.0
allowed-tools: Read
---

# GitLab Merge Request Creator

## Context

- Current git status: !`git status --porcelain`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -5`
- GitLab remote: !`git remote -v | grep gitlab | head -1`
- glab version: !`glab version 2>/dev/null || echo "NOT_INSTALLED"`
- glab auth status: !`glab auth status 2>&1 || echo "NOT_AUTHENTICATED"`
- Unpushed commits: !`git log @{u}..HEAD --oneline 2>/dev/null || echo "NO_UPSTREAM"`

## Your Task

Create a GitLab merge request using the `glab` CLI tool.

### Prerequisites

1. Verify `glab` is installed. If not, inform the user they need to install it:
   ```bash
   brew install glab  # macOS
   ```
2. Verify the current directory is a git repository with a GitLab remote.
3. Ensure there are commits to create an MR from (not on main/master without changes).

### Pre-Flight Checklist

Before creating the MR, verify:

- [ ] glab is installed and authenticated
- [ ] Not on main/master branch
- [ ] Has commits to merge (not empty)
- [ ] No uncommitted changes (warn if present)
- [ ] Branch has upstream or can be pushed

### Default Behavior

Use `--fill --fill-commit-body --yes` flags to:
- Auto-populate title from the first commit message
- Auto-populate description from commit messages AND commit bodies
- Skip confirmation prompts
- Push the branch if needed

### Base Command

```bash
glab mr create --fill --fill-commit-body --yes
```

> **Why `--fill-commit-body`?**
> The `--fill` flag alone only uses commit subject lines for the description. When there's only one commit or commits have no separate body text, the description ends up empty. `--fill-commit-body` ensures the full commit message (including body) is included in the MR description.

### Optional Arguments to Support

The user may specify these options. Parse them from the request:

| Option | Flag | Description |
|--------|------|-------------|
| Title | `-t` or `--title` | Custom MR title (overrides --fill) |
| Description | `-d` or `--description` | Custom MR description (overrides --fill) |
| Target branch | `-b` or `--target-branch` | Default: `main` or `master` |
| Source branch | `-s` or `--source-branch` | Explicit source branch |
| Assignee | `-a` or `--assignee` | Username(s) to assign |
| Reviewer | `--reviewer` | Username(s) to request review from |
| Labels | `-l` or `--label` | Comma-separated label names |
| Draft | `--draft` or `--wip` | Mark as draft MR |
| Related issue | `-i` or `--related-issue` | Create MR for an issue |
| Copy issue labels | `--copy-issue-labels` | Copy labels from related issue |
| Remove source branch | `--remove-source-branch` | Delete branch after merge |
| Milestone | `-m` or `--milestone` | Milestone ID or title |
| Allow collaboration | `--allow-collaboration` | Allow commits from other members |
| Squash on merge | `--squash-before-merge` | Squash commits when merging |
| Signoff | `--signoff` | Add DCO signoff to description |
| Open in browser | `-w` or `--web` | Continue creation in browser |
| Recovery | `--recover` | Save/restore options if creation fails |

### Step-by-Step Execution

1. **Check environment**: Verify glab is installed and authenticated, git repo has GitLab remote
2. **Check branch**: If on main/master, inform user they need to create a feature branch first
3. **Check for changes**: If no commits, inform user they need to commit changes first
4. **Warn about uncommitted changes**: If working directory is dirty, warn user before proceeding
5. **Build command**: Start with `glab mr create --fill --fill-commit-body --yes` and add any user-specified options
6. **Execute**: Run the glab command and report the result
7. **Report output**: Show the MR URL, number, and any relevant details

### Creating MR from an Issue

If the user mentions an issue number:

User says: "Create MR for issue #42"
→ Run: `glab mr create --fill --fill-commit-body --yes -i 42 --copy-issue-labels`

This automatically:
- Links the MR to the issue
- Copies labels from the issue
- Uses the issue title if no commits exist

### Draft MR Pattern

For work-in-progress, recommend draft MRs:

User says: "Create WIP MR" or "Draft MR for early feedback"
→ Run: `glab mr create --fill --fill-commit-body --yes --draft -l "WIP"`

Benefits:
- Signals work is in progress
- Prevents accidental merge
- Enables early code review

### Interactive Mode

If the user wants to edit the description interactively:

User says: "Create MR and let me edit it" or "Open MR in browser"
→ Run: `glab mr create --fill --fill-commit-body --web`

This opens the MR creation page in the browser for final editing.

### Common Errors and Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| `authentication required` | Not logged in | Run `glab auth login` |
| `no upstream branch` | Branch not pushed | Use `--push` or push manually first |
| `branch already exists remotely` | Push conflict | Pull and rebase, or force push with caution |
| `pipeline failed` | CI checks failed | Fix failing tests before creating MR |
| `merge conflicts detected` | Conflicts with target | Rebase onto target branch and resolve |
| `GLab requires authentication` | Token expired | Run `glab auth login --hostname <gitlab-host>` |

### Edge Cases to Handle

- **Not a git repo**: Inform user they need to be in a git repository
- **No GitLab remote**: Inform user the repo doesn't have a GitLab remote
- **glab not installed**: Provide installation instructions
- **Not authenticated**: Run `glab auth login` if needed
- **No commits**: Inform user they need to commit changes first
- **Already on main/master**: Ask user which branch to create MR from
- **Unpushed commits**: The `--fill` flag handles pushing, but inform user if it fails
- **Uncommitted changes**: Warn user their working directory has changes not in the MR

### Example Usage

User says: "Create an MR for this branch"
→ Run: `glab mr create --fill --fill-commit-body --yes`

User says: "Create MR for staging, assign to @john"
→ Run: `glab mr create --fill --fill-commit-body --yes -b staging -a john`

User says: "Create draft MR with bug label"
→ Run: `glab mr create --fill --fill-commit-body --yes --draft -l bug`

User says: "Create MR with custom description"
→ Run: `glab mr create -t "Fix bug" -d "Detailed description..." --yes`

User says: "Create MR for issue #123"
→ Run: `glab mr create --fill --fill-commit-body --yes -i 123 --copy-issue-labels`

User says: "Create MR and open in browser"
→ Run: `glab mr create --fill --fill-commit-body --web`

### Output Format

After creating the MR, provide a summary like:

```markdown
## Merge Request Created

- **MR**: !123
- **URL**: https://gitlab.com/group/repo/-/merge_requests/123
- **Branch**: feature-branch → main
- **Status**: Open
```

If the command fails, explain the error and suggest remediation steps.
