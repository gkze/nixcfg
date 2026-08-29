# nixcfg

[![License][c]][d] [![Last commit][e]][f]
[![Commit activity][g]][f] [![Nix flake][h]][i]
[![Platforms][j]][k]

Unified Nix flake for macOS hosts, Home Manager user configuration, and reusable
module building blocks.

This repository is still tailored to George's machines, user profile, and
workflows today. Ongoing work is focused on separating those personal defaults
into reusable framework primitives and a standalone library of modules.

## Current state

- Primary focus is [`nix-darwin`](https://github.com/LnL7/nix-darwin) plus
  [Home Manager](https://github.com/nix-community/home-manager).
- Active Darwin hosts: [`argus`](darwin/argus.nix) (work profile enabled) and
  [`rocinante`](darwin/rocinante.nix) (personal profile).
- Active Home Manager output: [`homeConfigurations.george`](flake.nix).
- Exported systems: [`aarch64-darwin`](flake.nix),
  [`aarch64-linux`](flake.nix), [`x86_64-linux`](flake.nix).
- NixOS modules are exported, but there are currently no
  [`nixosConfigurations`](flake.nix) defined.

## Repository layout

- [`darwin/`](darwin/): host entrypoints.
- [`home/`](home/): user configuration ([`home/george`](home/george/)).
- [`modules/`](modules/): reusable modules
  ([`common`](modules/common.nix), [`darwin`](modules/darwin/),
  [`nixos`](modules/nixos/), [`home`](modules/home/)).
- [`packages/`](packages/): custom package outputs
  ([`axiom-cli`](packages/axiom-cli/),
  [`codex-desktop`](packages/codex-desktop/),
  [`conductor`](packages/conductor/), [`droid`](packages/droid/),
  [`gogcli`](packages/gogcli/),
  [`homebrew-zsh-completion`](packages/homebrew-zsh-completion/),
  [`linear-cli`](packages/linear-cli/),
  [`nix-manipulator`](packages/nix-manipulator/),
  [`scratch`](packages/scratch/), [`sculptor`](packages/sculptor/),
  [`sublime-kdl`](packages/sublime-kdl.nix),
  [`superset`](packages/superset/), [`toad`](packages/toad/)).
- [`overlays/`](overlays/): package overrides and source pinning.
- [`lib/`](lib/): Python libraries for update tooling and Nix model/schema
  helpers.
- [`nixcfg.py`](nixcfg.py): Typer CLI exposed through
  [`nix run .#nixcfg -- ...`](nixcfg.py).

## Install and apply

1. Install Nix (recommended:
   [Determinate Nix Installer](https://github.com/DeterminateSystems/nix-installer)).
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

# Format and evaluate. The default no-build pass checks the current system;
# the all-systems pass is the full purity matrix. Neither may inspect outputs.
nix fmt
nix flake check --no-build --option allow-import-from-derivation false
nix flake check --all-systems --no-build --option allow-import-from-derivation false
nix flake check

# Pre-commit hooks
prek run -a

# Individual quality checks
uv run coverage run -m pytest
uv run coverage report

# Python test suite
uv run pytest

# Mutation testing with cosmic-ray
uv run cosmic-ray init cosmic-ray.toml .cosmic-ray.sqlite
uv run cosmic-ray exec cosmic-ray.toml .cosmic-ray.sqlite
uv run cr-report .cosmic-ray.sqlite
```

## Update tooling

Updates and package-artifact maintenance are explicit CLI operations; the
repository does not track GitHub Actions workflows.

```bash
nix run .#nixcfg -- --help
nix run .#nixcfg -- update --help
nix run .#nixcfg -- ci --help
nix run .#nixcfg -- schema --help
```

## Reuse as a framework

This flake can be consumed by another repository as a module framework.
Public API version 2 removes the site-specific `nixcfgProfiles` exports and
the `mkDarwinHost.work` policy shortcut. It also stops importing `sops-nix`
through `mkHomeModules`. Downstream configurations should import their own
profile modules and, when needed, the `sops-nix` Home Manager module explicitly.
Cache policy is now opt-in: the common substituter and trusted-key options
default to empty lists. `mkDarwinHost` also enables the Rosetta builder by
default without consulting ambient CI state; CI and other callers without a
Linux builder must pass `enableRosettaBuilder = false` explicitly.

- Exported `darwinModules`, `nixosModules`, and `homeModules` are declared in
  [`lib/exports.nix`](lib/exports.nix), the canonical module inventory.

- Exported constructors in [`lib`](lib/lib.nix):

  - [`mkSystem`](lib/lib.nix), [`mkDarwinHost`](lib/lib.nix),
    [`mkHome`](lib/lib.nix), [`mkHomeModules`](lib/lib.nix),
    [`mkSetOpencodeEnvModule`](lib/lib.nix)

- Downstream-oriented controls:

  - [`mkHome`](lib/lib.nix) supports `extraSpecialArgs` for
    downstream-only module arguments
  - [`mkSystem`](lib/lib.nix) supports `extraSpecialArgs`,
    `homeManagerExtraSpecialArgs`, and `homeModuleArgsByUser`. Darwin systems
    require at least one user; userless NixOS systems set `primaryUser = null`.
  - [`mkDarwinHost`](lib/lib.nix) forwards `extraSpecialArgs`,
    `homeManagerExtraSpecialArgs`, and `homeModuleArgsByUser`; it also supports
    `includeDefaultUserModule = false`, `homeModulesByUser`, and a custom `system`.
  - [`default.nix`](default.nix) and its `mkLib` helper accept an explicit
    `evaluationContext` for update source overrides and fake-hash evaluation.
    Ambient environment variables do not alter the API.

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

Site-specific policy (for example cache keys, org profile settings, host/user
modules) should live in the consuming repository, while these shared modules
stay generic.

## License

[MIT](LICENSE)

[c]: https://img.shields.io/github/license/gkze/nixcfg?style=flat-square
[d]: https://github.com/gkze/nixcfg/blob/main/LICENSE
[e]: https://img.shields.io/github/last-commit/gkze/nixcfg/main?style=flat-square
[f]: https://github.com/gkze/nixcfg/commits/main
[g]: https://img.shields.io/github/commit-activity/m/gkze/nixcfg?style=flat-square
[h]: https://img.shields.io/badge/Nix-flake-5277C3?logo=nixos&logoColor=white&style=flat-square
[i]: https://nixos.org
[j]: https://img.shields.io/badge/platform-aarch64--darwin%20%7C%20aarch64--linux%20%7C%20x86_64--linux-334155?style=flat-square
[k]: https://github.com/gkze/nixcfg#current-state
