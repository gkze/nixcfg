# Code Review: nixcfg Repository

## Executive Summary

Your Nix configuration is well-structured with strong foundations in modularity, input management, and cross-platform support. The codebase demonstrates solid Nix knowledge with good use of helper functions (`mkHome`, `mkSystem`) and proper overlay composition. Below are prioritized suggestions for improvement.

______________________________________________________________________

## Critical Issues

### 1. SSH Security Vulnerability

**File:** `home/george/configuration.nix:207`

```nix
StrictHostKeyChecking = "no"
```

- **Risk:** Makes MITM attacks possible for all SSH connections
- **Fix:** Only disable for specific trusted hosts, or remove entirely

### 2. Global allowInsecure Flag

**File:** `modules/common.nix:62`

```nix
allowInsecure = true;
```

- **Risk:** May allow installation of packages with known vulnerabilities
- **Fix:** Remove or document why it's necessary; use per-package overrides instead

### 3. Hardcoded 1Password Path

**File:** `modules/home/town.nix:16`

```nix
HOME=$(mktemp -d) /opt/homebrew/bin/op completion zsh > $out/_op
```

- **Risk:** Breaks on Intel Macs (`/usr/local/bin`) or if 1Password not installed
- **Fix:** Use `pkgs._1password` or make conditional with `lib.optionalAttrs`

______________________________________________________________________

## High Priority Improvements

### 4. Split Large nixvim.nix (902 lines)

**File:** `home/george/nixvim.nix`

The file is too large to maintain effectively. Recommended split:

```
home/george/nixvim/
├── default.nix       # Main entry, imports all
├── plugins.nix       # Plugin configurations (~400 lines)
├── keymaps.nix       # Keybindings (~130 lines)
├── lsp.nix           # LSP servers (~100 lines)
├── colorscheme.nix   # Theme settings
└── autocmds.nix      # Autocommands
```

### 5. Split Monolithic overlays.nix (226 lines)

**File:** `overlays.nix`

Currently mixes Go, Python, Rust, and Vim plugins. Recommended:

```
overlays/
├── default.nix        # Imports all overlays
├── go-packages.nix    # beads, stars
├── python-packages.nix # beads-mcp, toad
├── vim-plugins.nix    # treesitter overrides, codesnap
└── utils.nix          # homebrew-zsh-completion, mountpoint-s3
```

### 6. Implement Secrets Management

**Current State:** No encryption; metadata in plain text (`meta.nix`)

**Recommended:** Implement `agenix` or `sops-nix`:

```nix
# Example with agenix
age.secrets.gpgKey.file = ../secrets/gpg-key.age;
programs.gpg.settings.default-key = config.age.secrets.gpgKey.path;
```

### 7. Consolidate Theme Configuration

Theme (Catppuccin Frappe) is configured in multiple places:

- `stylix.nix:7` - base theme
- `nixvim.nix:67-99` - explicit colorscheme
- `configuration.nix:112-116` - bat theme
- `git.nix:49` - delta theme

**Fix:** Create single `theme.nix` module with variables other modules reference

______________________________________________________________________

## Medium Priority Improvements

### 8. Reduce Machine Configuration Duplication

**Files:** `darwin/argus.nix`, `darwin/rocinante.nix`

~80% identical code. Create a parameterized base:

```nix
# darwin/default.nix
{ hostname, extraHomeModules ? [], extraSystemModules ? [] }:
mkSystem {
  system = "aarch64-darwin";
  users = [ "george" ];
  homeModules = [ ./modules/home/macbook-pro-16in.nix ] ++ extraHomeModules;
  systemModules = baseModules ++ extraSystemModules;
}
```

### 9. Document Disabled Features

Several features are disabled without explanation:

- `zsh.nix:255` - Zellij integration disabled
- `nixvim.nix:665` - spectre_oxi plugin (TODO comment)
- `nixvim.nix:644` - kulala plugin commented out
- `stylix.nix:9-16` - bat, nixvim, vscode targets disabled

Add comments explaining *why* each is disabled.

### 10. Consolidate Dock Configuration

**Files:**

- `modules/darwin/george/dock-apps.nix` (24 lines)
- `modules/darwin/george/town-dock-apps.nix` (27 lines)

85% overlap. Merge into single parametrized module:

```nix
{ config, lib, ... }: {
  options.dock.extraApps = lib.mkOption { default = []; };
  config.system.defaults.dock.persistent-apps =
    baseApps ++ config.dock.extraApps;
}
```

### 11. Fix Wallpaper Path Issue

**File:** `stylix.nix:7`

```nix
image = ./wallpaper.jpeg;
```

- Local file not tracked in git; may break on fresh clone
- **Fix:** Add to git or use URL with `pkgs.fetchurl`

### 12. Parameterize Hardware-Specific Values

**File:** `modules/home/macbook-pro-16in.nix:2-12`

```nix
dimensions = { columns = 250; lines = 80; };
position = { x = 433; y = 400; };
```

- Magic numbers not portable across displays
- **Fix:** Make configurable via module options or remove

______________________________________________________________________

## Low Priority Improvements

### 13. Add Package Categories

**File:** `home/george/packages.nix`

80+ packages with no organization. Add comment sections:

```nix
home.packages = with pkgs; [
  # === CLI Tools ===
  bat bottom eza fd fzf ripgrep

  # === Development ===
  git-lfs gh gitui

  # === TUI Applications ===
  yazi lazygit

  # === Languages ===
  nodejs bun
];
```

### 14. Clean Up Commented Code

Several files have commented-out code that should be removed or documented:

- `git.nix:38-42` - difftastic (replaced by delta)
- `configuration.nix:153-167` - ghostty config duplicate
- `go.nix:15-19` - go zsh plugin
- `packages.nix:6` - gitbutler

### 15. Add Input Documentation

**File:** `flake.nix`

28 non-flake inputs lack explanation. Add comments:

```nix
# Vim plugins not available in nixpkgs
zsh-system-clipboard = { url = "..."; flake = false; };
```

### 16. Add Flake Checks

Currently no validation. Add:

```nix
checks = {
  pre-commit = pre-commit-hooks.run {
    hooks = {
      nixfmt.enable = true;
      statix.enable = true;
    };
  };
};
```

______________________________________________________________________

## Code Quality Metrics

| Metric           | Value                       | Assessment        |
| ---------------- | --------------------------- | ----------------- |
| Total Nix files  | ~30                         | Reasonable        |
| Largest file     | 902 lines (nixvim.nix)      | Too large         |
| Input count      | 68 (40 flake, 28 non-flake) | High but managed  |
| Code duplication | ~15%                        | Improvable        |
| Documentation    | Minimal                     | Needs improvement |
| Security         | 2 issues                    | Needs attention   |

______________________________________________________________________

## Best Practices Already Followed

1. **Input pinning via `.follows`** - Excellent version coherence
1. **Library abstraction** - `mkHome`/`mkSystem` reduce boilerplate
1. **Platform-aware configs** - Darwin/Linux properly separated
1. **Treefmt integration** - Comprehensive formatter setup
1. **Overlay composition** - Using `composeManyExtensions` correctly
1. **Touch ID for sudo** - Good UX improvement
1. **GPG signing** - Properly configured for commits
1. **SSH agent via launchd** - Clean daemon management

______________________________________________________________________

## Summary of Recommendations

| Priority | Issue                         | Effort    |
| -------- | ----------------------------- | --------- |
| Critical | Fix SSH StrictHostKeyChecking | 5 min     |
| Critical | Review allowInsecure flag     | 10 min    |
| Critical | Fix hardcoded 1Password path  | 15 min    |
| High     | Split nixvim.nix              | 1-2 hours |
| High     | Split overlays.nix            | 30 min    |
| High     | Implement secrets management  | 2-3 hours |
| Medium   | Reduce machine duplication    | 30 min    |
| Medium   | Document disabled features    | 20 min    |
| Medium   | Consolidate dock config       | 20 min    |
| Low      | Add package categories        | 15 min    |
| Low      | Clean up commented code       | 15 min    |
