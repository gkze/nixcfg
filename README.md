# nixcfg

[![Update workflow](https://github.com/gkze/nixcfg/actions/workflows/update.yml/badge.svg)](https://github.com/gkze/nixcfg/actions/workflows/update.yml) [![License](https://img.shields.io/github/license/gkze/nixcfg?style=flat-square)](https://github.com/gkze/nixcfg/blob/main/LICENSE) [![Last commit](https://img.shields.io/github/last-commit/gkze/nixcfg/main?style=flat-square)](https://github.com/gkze/nixcfg/commits/main) [![Commit activity](https://img.shields.io/github/commit-activity/m/gkze/nixcfg?style=flat-square)](https://github.com/gkze/nixcfg/commits/main) [![Nix flake](https://img.shields.io/badge/Nix-flake-5277C3?logo=nixos&logoColor=white&style=flat-square)](https://nixos.org) [![Platforms](https://img.shields.io/badge/platform-aarch64--darwin%20%7C%20aarch64--linux%20%7C%20x86_64--linux-334155?style=flat-square)](https://github.com/gkze/nixcfg#current-state) [![Update cadence](https://img.shields.io/badge/update-every%206h-0ea5e9?style=flat-square)](https://github.com/gkze/nixcfg/actions/workflows/update.yml)

Unified Nix flake for macOS hosts, Home Manager user configuration, and reusable module building blocks.

## Current state

- Primary focus is `nix-darwin` plus Home Manager.
- Active Darwin hosts: `argus` (work profile enabled) and `rocinante` (personal profile).
- Active Home Manager output: `homeConfigurations.george`.
- Exported systems: `aarch64-darwin`, `aarch64-linux`, `x86_64-linux`.
- NixOS modules are exported, but there are currently no `nixosConfigurations` defined.

## Repository layout

- `darwin/`: host entrypoints.
- `home/`: user configuration (`home/george`).
- `modules/`: reusable modules (`common`, `darwin`, `nixos`, `home`).
- `packages/`: custom package outputs (`axiom-cli`, `beads-mcp`, `conductor`, `droid`, `gogcli`, `homebrew-zsh-completion`, `linear-cli`, `nix-manipulator`, `scratch`, `sculptor`, `sublime-kdl`, `toad`).
- `overlays/`: package overrides and source pinning.
- `lib/`: Python libraries for update tooling and Nix model/schema helpers.
- `nixcfg.py`: Typer CLI exposed through `nix run .#nixcfg -- ...`.

## Install and apply

1. Install Nix (recommended: [Determinate Nix Installer](https://github.com/DeterminateSystems/nix-installer)).
1. Clone this repository to `~/.config/nixcfg`.
1. Apply the Darwin configuration:

```bash
nh darwin switch --no-nom .
```

Useful build-only checks:

```bash
nix build .#darwinConfigurations.argus.system
nix build .#darwinConfigurations.rocinante.system
nix build .#homeConfigurations.george.activationPackage
```

## Day-to-day commands

```bash
# Enter the dev environment (tooling + pre-commit hooks)
nix develop

# Keep Python tooling in sync for editor/test workflows
uv sync

# Format and evaluate
nix fmt
nix flake check

# Python test suite
uv run pytest
```

## Update automation

The repo ships a dedicated update CLI:

```bash
nix run .#nixcfg -- --help
nix run .#nixcfg -- update --help
nix run .#nixcfg -- ci --help
nix run .#nixcfg -- schema --help
```

GitHub Actions workflow `.github/workflows/update.yml` runs every 6 hours and:

- updates `flake.lock`
- resolves upstream versions once
- computes per-platform `sources.json` hashes
- builds Darwin outputs (`argus`, `rocinante`)
- opens a signed PR with update details

## Reuse as a framework

This flake can be consumed by another repository as a module framework.

- Exported module sets:

  - `darwinModules` (`nixcfgCommon`, `nixcfgBase`, `nixcfgProfiles`, `nixcfgHomebrew`)
  - `nixosModules` (`nixcfgCommon`, `nixcfgBase`, `nixcfgProfiles`)
  - `homeModules` (`nixcfgBase`, `nixcfgGit`, `nixcfgProfiles`, `nixcfgPackages`, `nixcfgOpencode`, `nixcfgTheme`, `nixcfgFonts`, `nixcfgStylix`, `nixcfgZsh`, `nixcfgDarwin`, `nixcfgLinux`, `nixcfgLanguageBun`, `nixcfgLanguageGo`, `nixcfgLanguagePython`, `nixcfgLanguageRust`)

- Exported constructors in `lib`:

  - `mkSystem`, `mkDarwinHost`, `mkHome`, `mkHomeModules`

- Downstream-oriented controls:

  - `mkHome` supports `extraSpecialArgs` for downstream-only module arguments
  - `mkSystem` supports `extraSpecialArgs`, `homeManagerExtraSpecialArgs`, `homeModuleArgsByUser`, and tolerates `users = [ ]` (it sets `primaryUser = null`)
  - `mkDarwinHost` forwards `extraSpecialArgs`, `homeManagerExtraSpecialArgs`, `homeModuleArgsByUser`, supports `includeDefaultUserModule = false`, `homeModulesByUser`, and custom `system`

- Policy knobs intended to be overridden in downstream repos:

  - `nixcfg.common.hostname`
  - `nixcfg.common.nix.substituters`
  - `nixcfg.common.nix.trustedPublicKeys`
  - `nixcfg.darwin.homebrew.{user,taps,mutableTaps,enableRosetta}`

Example downstream pattern:

```nix
{
  outputs = { nixcfg, ... }: {
    darwinConfigurations.my-host = nixcfg.lib.mkDarwinHost {
      user = "alice";
      includeDefaultUserModule = false;

      extraSpecialArgs = {
        org = "acme";
      };
      homeManagerExtraSpecialArgs = {
        privateRoot = ./.;
      };
      homeModuleArgsByUser.alice = {
        role = "platform";
      };

      extraHomeModules = [
        nixcfg.homeModules.nixcfgBase
        nixcfg.homeModules.nixcfgGit
        ./home/alice.nix
      ];

      extraSystemModules = [
        {
          nixcfg.common.nix.substituters = [ "https://cache.nixos.org" ];
          nixcfg.common.nix.trustedPublicKeys = [
            "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
          ];
        }
      ];
    };
  };
}
```

Site-specific policy (for example cache keys, org profile settings, host/user modules) should live in the consuming repository, while these shared modules stay generic.

## License

[MIT](LICENSE)
