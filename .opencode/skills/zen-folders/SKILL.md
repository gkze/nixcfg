______________________________________________________________________

## name: zen-folders description: Declarative pinned-tab folder management for Zen browser (Twilight.app). Use this skill when the user wants to manage, reorganize, or inspect tab folders in their Zen browser session via a YAML config file.

## What I do

Manage pinned tab folders in Zen browser declaratively. A YAML config file (`home/george/zen-folders.yaml`) is the source of truth for folder structure, tab assignments, and ordering. The `zen-folders` CLI reconciles the browser session to match the config.

## When to use me

Use this when the user wants to:

- Add, remove, or reorder tab folders
- Move tabs between folders or change folder membership
- Inspect current folder/tab state
- Bootstrap a new YAML config from the current session
- Debug folder/session structural issues

## Background

Zen is a Firefox fork installed at `/Applications/Twilight.app`. It stores session data in a Mozilla LZ4-compressed JSON file (`zen-sessions.jsonlz4`). Folders require entries in **two** arrays:

1. **`groups`** -- Firefox creates native `tab-group` DOM elements
1. **`folders`** -- Zen upgrades those DOM nodes into `zen-folder` elements

Both must have matching entries for folders to render. Each folder also needs a hidden empty placeholder tab with `zenIsEmpty: true`.

**Zen must be closed** when making write changes -- it overwrites the session from DOM state on quit.

## Key design decisions

- **List order IS render order** -- folder order in YAML = `prevSiblingInfo` chain; tab order within a folder = position in session `tabs` array
- **Workspace referenced by name** (e.g. `"Work"`), resolved to UUID at runtime via the `spaces` array
- **Tab matching by URL substring** (case-insensitive) -- use domain names or unique URL fragments as patterns
- **No new tab creation** from YAML -- only controls folder structure, ordering, and assignment of existing pinned tabs

## CLI location

```
~/.local/bin/zen-folders
```

Source: `home/george/bin/zen-folders` (deployed via home-manager `home.file`)

## Config file

```
home/george/zen-folders.yaml
```

Default location: `~/.config/nixcfg/home/george/zen-folders.yaml`

### YAML format

```yaml
Work:
  Infrastructure:
    Fly.io: fly.io
    Render: dashboard.render.com
  Email:
    Resend: resend.com
    SendGrid: sendgrid.com
```

Top-level key is the workspace name. Under it, each key is a folder name. Under
each folder, keys are tab titles and values are URL match patterns.

**URL patterns**: Use domain names or unique URL fragments. Avoid short patterns that could match unintended URLs (e.g. `x.com` matches `dropbox.com`; use `//x.com` instead).

## Session file

```
~/Library/Application Support/zen/Profiles/ecrjha3i.Default (twilight)/zen-sessions.jsonlz4
```

## Commands

### Read-only (safe while Zen is running)

```bash
# List all folders in order with tab counts
zen-folders list
zen-folders list -v          # also show tab URLs

# List all pinned tabs grouped by folder
zen-folders tabs

# Generate YAML from current session (bootstrap)
zen-folders dump
zen-folders dump -o zen-folders.yaml  # write to file

# Show what changes would be made (dry run)
zen-folders diff
zen-folders diff -c /path/to/custom.yaml
```

### Write commands (Zen must be closed)

```bash
# Reconcile session with YAML config
zen-folders apply
zen-folders apply -y         # skip confirmation prompt
zen-folders apply -c /path/to/custom.yaml
```

Backups are created automatically before any write.

### Structural check

```bash
zen-folders check           # verify groups/folders/tabs consistency
```

### Global options

```bash
-p, --profile PROFILE    # Zen profile dir (default: ecrjha3i.Default (twilight))
-w, --workspace NAME     # Workspace name (default: Work)
```

## Workflow for reorganizing folders

1. Inspect current state: `zen-folders list -v` or `zen-folders tabs`
1. Edit `home/george/zen-folders.yaml` -- add/remove/reorder folders and tabs
1. Preview changes: `zen-folders diff`
1. Close Zen browser
1. Apply: `zen-folders apply`
1. Open Zen and verify

## Workflow for bootstrapping from scratch

1. Set up folders manually in Zen (or they already exist)
1. Close Zen
1. Run `zen-folders dump -o home/george/zen-folders.yaml`
1. Edit the generated YAML to clean up URLs to short patterns and add titles
1. Verify: `zen-folders diff` should show no changes

## Troubleshooting

- **Folders don't appear**: Check that both `groups` and `folders` arrays have matching entries (`zen-folders check`)
- **Zen overwrites changes**: Always close Zen before running `apply`
- **Tab not matching**: Patterns are case-insensitive URL substrings; use `zen-folders tabs` to see exact URLs. Avoid patterns that are substrings of other URLs.
- **URL pattern matches multiple tabs**: The first match is used and a warning is shown. Make patterns more specific.
- **Process detection**: The CLI inspects running processes and matches Zen executable paths (Twilight/Zen app bundles)

## Technical details for modifying the script

Source: `home/george/bin/zen-folders`. Key internals:

- **Session I/O**: `read_session()` / `write_session()` handle Mozilla LZ4 format (8-byte `mozLz40\0` magic + 4-byte LE uncompressed size + lz4 block)
- **Workspace resolution**: `resolve_workspace()` maps names like `"Work"` to UUIDs via the `spaces` array
- **Reconciliation engine**: `compute_plan()` diffs session vs config; `apply_plan()` mutates session
- **Folder creation** requires three things: a `groups[]` entry, a `folders[]` entry, and an empty placeholder tab
- **Tab assignment**: Set the tab's `groupId` field to the folder's `id`
- **Ordering**: `prevSiblingInfo` chain for folders; position in `tabs` array for tabs within a folder
- **Dependencies**: `python3Packages.lz4` + `python3Packages.pyyaml` (provided via nix-shell shebang)
