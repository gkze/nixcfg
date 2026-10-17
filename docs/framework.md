# Framework reuse

This flake can serve as a module framework for another repository. This document
lists the exported module sets, the constructors, and the controls that
downstream repositories use.

Read [architecture.md](architecture.md) for the repository layout, and
[development.md](development.md) for the build workflow.

## Public API version 2

Version 2 makes these changes:

- It removes the site-specific `nixcfgProfiles` exports and the
  `mkDarwinHost.work` policy shortcut.
- It stops importing `sops-nix` through `mkHomeModules`. A downstream
  configuration imports its own profile modules and, when needed, the
  `sops-nix` Home Manager module explicitly.
- Cache policy is opt-in. The common substituter and trusted-key options default
  to empty lists.
- `mkDarwinHost` enables the Rosetta builder by default, without consulting
  ambient CI state. CI and other callers without a Linux builder must pass
  `enableRosettaBuilder = false` explicitly.

## Exported module sets

`lib/exports.nix` is the canonical module inventory. It exports three sets.

`darwinModules`:

- `nixcfgCommon`
- `nixcfgBase`
- `nixcfgHomebrew`

`nixosModules`:

- `nixcfgCommon`
- `nixcfgBase`

`homeModules`:

- `nixcfgBase`, `nixcfgGit`, `nixcfgPackages`, `nixcfgOpencode`
- `nixcfgTheme`, `nixcfgFonts`, `nixcfgStylix`, `nixcfgZsh`
- `nixcfgZen`, `nixcfgDarwin`, `nixcfgLinux`
- `nixcfgLanguageBun`, `nixcfgLanguageGo`, `nixcfgLanguagePython`,
  `nixcfgLanguageRust`

## Required module arguments

Some exported modules read module arguments in addition to the standard
`config`, `lib`, `pkgs`, and `options`. The constructors `mkSystem`,
`mkDarwinHost`, and `mkHome` supply all of them. A consumer that imports a
module directly into its own `homeManagerConfiguration`, `darwinSystem`, or
`nixosSystem` must supply them through `specialArgs` or `extraSpecialArgs`.

| Module                             | Argument                               | Required content                                                       |
| ---------------------------------- | -------------------------------------- | ---------------------------------------------------------------------- |
| `homeModules.nixcfgBase`           | `slib`                                 | This flake's `lib`: `homeDirBase`                                      |
| `homeModules.nixcfgBase`           | `src`                                  | A source tree with a `NIXOS_VERSION` file                              |
| `homeModules.nixcfgBase`           | `system`                               | The Nix system string                                                  |
| `homeModules.nixcfgBase`           | `username`                             | The Home Manager user name                                             |
| `homeModules.nixcfgGit`            | `inputs`                               | `catppuccin-delta`, read when `includeCatppuccinDeltaTheme` is enabled |
| `homeModules.nixcfgStylix`         | `inputs`                               | `base16-schemes-src`, read when `base16Scheme` is null                 |
| `homeModules.nixcfgZsh`            | `slib`                                 | This flake's `lib`: `srcDirBase`                                       |
| `homeModules.nixcfgZsh`            | `system`                               | The Nix system string                                                  |
| `homeModules.nixcfgLanguagePython` | `slib`                                 | This flake's `lib`: `kernel`                                           |
| `darwinModules.nixcfgBase`         | `primaryUser`                          | The primary Darwin user name                                           |
| `darwinModules.nixcfgCommon`       | `inputs`, `slib`, `hostname`, `system` | Optional: flake registry entries, kernel lookup, and host name         |
| `darwinModules.nixcfgHomebrew`     | `inputs`, `primaryUser`                | Optional: Homebrew source and taps, and the default Homebrew user      |

Pass `slib = nixcfg.lib` and the flake inputs of this repository (or inputs
with the same names) to satisfy them.

## Module-owned tooling

`homeModules.nixcfgZen` packages its own `zentool` from `modules/home/zen/`.
Override `nixcfg.zen.toolPackage` to supply a different build, or
`nixcfg.zen.toolCommand` to run a command outside the Nix store.

## Constructors

`lib/lib.nix` exports these constructors:

- `mkSystem`
- `mkDarwinHost`
- `mkHome`
- `mkHomeModules`
- `mkSetOpencodeEnvModule`

## Downstream controls

- `mkHome` supports `extraSpecialArgs` for downstream-only module arguments.
- `mkSystem` supports `extraSpecialArgs`, `homeManagerExtraSpecialArgs`, and
  `homeModuleArgsByUser`. Darwin systems require at least one user. A userless
  NixOS system sets `primaryUser = null`.
- `mkDarwinHost` forwards `extraSpecialArgs`, `homeManagerExtraSpecialArgs`, and
  `homeModuleArgsByUser`. It also supports `includeDefaultUserModule = false`,
  `homeModulesByUser`, and a custom `system`.
- `default.nix` and its `mkLib` helper accept an explicit `evaluationContext`
  for update source overrides and fake-hash evaluation. Ambient environment
  variables do not alter the API.

## Policy knobs

These options are intended for override in a downstream repository:

- `nixcfg.common.hostname`
- `nixcfg.common.nix.substituters`
- `nixcfg.common.nix.trustedPublicKeys`
- `nixcfg.darwin.homebrew.{user,taps,mutableTaps,enableRosetta}`

Site-specific policy, such as cache keys, org profile settings, and host or user
modules, belongs in the consuming repository. The shared modules stay generic.

## Downstream example

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

## Related

- [architecture.md](architecture.md): layout, outputs, and module layers
- [development.md](development.md): dev shell, checks, and quality gates
- [packaging.md](packaging.md): package and overlay discovery
- [README.md](../README.md): install and apply instructions
