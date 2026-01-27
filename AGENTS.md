# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Landing the Plane (Session Completion)

**When ending a work session**, complete ALL applicable steps below.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
1. **Run quality gates** (if code changed) - Tests, linters, builds
   - For Nix configuration changes: `nh darwin switch --no-nom .`
1. **Update issue status** - Close finished work, update in-progress items
1. **Commit changes** - Stage and commit all work:
   ```bash
   git add <files>
   git commit -S -m "message"
   bd sync
   ```
1. **Hand off** - Provide context for next session

**CRITICAL RULES:**

- ALWAYS sign commits with `-S` flag (e.g., `git commit -S -m "message"`)
- Do NOT push automatically - let the user decide when to push
