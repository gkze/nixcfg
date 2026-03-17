# nixcfg

[![Update workflow](https://github.com/gkze/nixcfg/actions/workflows/update.yml/badge.svg)](https://github.com/gkze/nixcfg/actions/workflows/update.yml) [![License](https://img.shields.io/github/license/gkze/nixcfg?style=flat-square)](https://github.com/gkze/nixcfg/blob/main/LICENSE) [![Last commit](https://img.shields.io/github/last-commit/gkze/nixcfg/main?style=flat-square)](https://github.com/gkze/nixcfg/commits/main) [![Commit activity](https://img.shields.io/github/commit-activity/m/gkze/nixcfg?style=flat-square)](https://github.com/gkze/nixcfg/commits/main) [![Nix flake](https://img.shields.io/badge/Nix-flake-5277C3?logo=nixos&logoColor=white&style=flat-square)](https://nixos.org) [![Platforms](https://img.shields.io/badge/platform-aarch64--darwin%20%7C%20aarch64--linux%20%7C%20x86_64--linux-334155?style=flat-square)](https://github.com/gkze/nixcfg#current-state) [![Update cadence](https://img.shields.io/badge/update-every%206h-0ea5e9?style=flat-square)](https://github.com/gkze/nixcfg/actions/workflows/update.yml)

Unified Nix flake for macOS hosts, Home Manager user configuration, and reusable module building blocks.

This repository is still tailored to George's machines, user profile, and workflows today.
Ongoing work is focused on separating those personal defaults into reusable framework
primitives and a standalone library of modules.

## Current state

- Primary focus is [`nix-darwin`](https://github.com/LnL7/nix-darwin) plus [Home Manager](https://github.com/nix-community/home-manager).
- Active Darwin hosts: [`argus`](darwin/argus.nix) (work profile enabled) and [`rocinante`](darwin/rocinante.nix) (personal profile).
- Active Home Manager output: [`homeConfigurations.george`](flake.nix#L296).
- Exported systems: [`aarch64-darwin`](flake.nix#L272), [`aarch64-linux`](flake.nix#L273), [`x86_64-linux`](flake.nix#L274).
- NixOS modules are exported, but there are currently no [`nixosConfigurations`](flake.nix) defined.

## Repository layout

- [`darwin/`](darwin/): host entrypoints.
- [`home/`](home/): user configuration ([`home/george`](home/george/)).
- [`modules/`](modules/): reusable modules ([`common`](modules/common.nix), [`darwin`](modules/darwin/), [`nixos`](modules/nixos/), [`home`](modules/home/)).
- [`packages/`](packages/): custom package outputs ([`axiom-cli`](packages/axiom-cli/), [`codex-desktop`](packages/codex-desktop/), [`conductor`](packages/conductor/), [`droid`](packages/droid/), [`gogcli`](packages/gogcli/), [`homebrew-zsh-completion`](packages/homebrew-zsh-completion/), [`linear-cli`](packages/linear-cli/), [`nix-manipulator`](packages/nix-manipulator/), [`scratch`](packages/scratch/), [`sculptor`](packages/sculptor/), [`sublime-kdl`](packages/sublime-kdl.nix), [`superset`](packages/superset/), [`toad`](packages/toad/)).
- [`overlays/`](overlays/): package overrides and source pinning.
- [`lib/`](lib/): Python libraries for update tooling and Nix model/schema helpers.
- [`nixcfg.py`](nixcfg.py): Typer CLI exposed through [`nix run .#nixcfg -- ...`](nixcfg.py).

## Install and apply

1. Install Nix (recommended: [Determinate Nix Installer](https://github.com/DeterminateSystems/nix-installer)).
1. Clone this repository to [`~/.config/nixcfg`](.).
1. Apply the Darwin configuration:

```bash
nh darwin switch --no-nom .
```

Useful build-only checks:

```bash
nix build .#checks.aarch64-darwin.darwin-argus
nix build .#checks.aarch64-darwin.darwin-rocinante
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

# Pre-commit hooks
prek run -a

# Individual quality checks
uv run coverage run -m pytest
uv run coverage report

# Python test suite
uv run pytest

# Mutation testing (full run)
uv run mutmut run --max-children 4

# Mutation testing (targeted rerun by mutant glob)
uv run mutmut run "lib.nix.commands.*"
uv run mutmut results
uv run mutmut browse

# Mutation testing with cosmic-ray (safer fallback on Python 3.14)
uv run cosmic-ray init cosmic-ray.toml .cosmic-ray.sqlite
uv run cosmic-ray exec cosmic-ray.toml .cosmic-ray.sqlite
uv run cr-report .cosmic-ray.sqlite
```

## Update automation

The repo ships a dedicated update CLI:

```bash
nix run .#nixcfg -- --help
nix run .#nixcfg -- update --help
nix run .#nixcfg -- ci --help
nix run .#nixcfg -- schema --help
```

GitHub Actions workflow [`.github/workflows/update.yml`](.github/workflows/update.yml) runs every 6 hours and:

- updates [`flake.lock`](flake.lock)
- resolves upstream versions once
- computes per-platform [`sources.json`](packages/toad/sources.json) hashes
- builds Darwin outputs ([`argus`](darwin/argus.nix), [`rocinante`](darwin/rocinante.nix))
- opens a signed PR with update details

## Reuse as a framework

This flake can be consumed by another repository as a module framework.

- Exported module sets:

  - [`darwinModules`](flake.nix#L305) ([`nixcfgCommon`](modules/common.nix), [`nixcfgBase`](modules/darwin/base.nix), [`nixcfgProfiles`](modules/darwin/profiles.nix), [`nixcfgHomebrew`](modules/darwin/homebrew.nix))
  - [`nixosModules`](flake.nix#L299) ([`nixcfgCommon`](modules/common.nix), [`nixcfgBase`](modules/nixos/base.nix), [`nixcfgProfiles`](modules/nixos/profiles.nix))
  - [`homeModules`](flake.nix#L312) ([`nixcfgBase`](modules/home/base.nix), [`nixcfgGit`](modules/home/git.nix), [`nixcfgProfiles`](modules/home/profiles.nix), [`nixcfgPackages`](modules/home/packages.nix), [`nixcfgOpencode`](modules/home/opencode.nix), [`nixcfgTheme`](modules/home/theme.nix), [`nixcfgFonts`](modules/home/fonts.nix), [`nixcfgStylix`](modules/home/stylix.nix), [`nixcfgZsh`](modules/home/zsh.nix), [`nixcfgDarwin`](modules/home/darwin.nix), [`nixcfgLinux`](modules/home/linux.nix), [`nixcfgLanguageBun`](modules/home/languages/bun.nix), [`nixcfgLanguageGo`](modules/home/languages/go.nix), [`nixcfgLanguagePython`](modules/home/languages/python.nix), [`nixcfgLanguageRust`](modules/home/languages/rust.nix))

- Exported constructors in [`lib`](lib.nix):

  - [`mkSystem`](lib.nix#L338), [`mkDarwinHost`](lib.nix#L451), [`mkHome`](lib.nix#L310), [`mkHomeModules`](lib.nix#L282)

- Downstream-oriented controls:

  - [`mkHome`](lib.nix#L310) supports [`extraSpecialArgs`](lib.nix#L315) for downstream-only module arguments
  - [`mkSystem`](lib.nix#L338) supports [`extraSpecialArgs`](lib.nix#L344), [`homeManagerExtraSpecialArgs`](lib.nix#L345), [`homeModuleArgsByUser`](lib.nix#L342), and tolerates [`users = [ ]`](lib.nix#L348) (it sets [`primaryUser = null`](lib.nix#L379))
  - [`mkDarwinHost`](lib.nix#L451) forwards [`extraSpecialArgs`](lib.nix#L461), [`homeManagerExtraSpecialArgs`](lib.nix#L462), [`homeModuleArgsByUser`](lib.nix#L459), supports [`includeDefaultUserModule = false`](lib.nix#L460), [`homeModulesByUser`](lib.nix#L458), and custom [`system`](lib.nix#L454)

- Policy knobs intended to be overridden in downstream repos:

  - [`nixcfg.common.hostname`](modules/common.nix)
  - [`nixcfg.common.nix.substituters`](modules/common.nix)
  - [`nixcfg.common.nix.trustedPublicKeys`](modules/common.nix)
  - [`nixcfg.darwin.homebrew.{user,taps,mutableTaps,enableRosetta}`](modules/darwin/homebrew.nix)

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
