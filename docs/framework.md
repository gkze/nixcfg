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
| `homeModules.nixcfgBase`           | `username`                             | The Home Manager user name                                             |
| `homeModules.nixcfgGit`            | `inputs`                               | `catppuccin-delta`, read when `includeCatppuccinDeltaTheme` is enabled |
| `homeModules.nixcfgStylix`         | `inputs`                               | `base16-schemes-src`, read when `base16Scheme` is null                 |
| `homeModules.nixcfgZsh`            | `slib`                                 | This flake's `lib`: `srcDirBase`                                       |
| `homeModules.nixcfgLanguagePython` | `slib`                                 | This flake's `lib`: `kernel`                                           |
| `darwinModules.nixcfgBase`         | `primaryUser`                          | The primary Darwin user name                                           |
| `darwinModules.nixcfgCommon`       | `inputs`, `slib`, `hostname`, `system` | Optional: flake registry entries, kernel lookup, and host name         |
| `darwinModules.nixcfgHomebrew`     | `inputs`, `primaryUser`                | Optional: Homebrew source and taps, and the default Homebrew user      |

Pass `slib = nixcfg.lib` and the flake inputs of this repository (or inputs
with the same names) to satisfy them.

`darwinModules.nixcfgHomebrew` also requires the `nix-homebrew` Darwin module.
`mkDarwinHost` imports both. `mkSystem` imports neither, so a `mkSystem` host
that wants managed Homebrew adds both to `systemModules`.

### Home module composition

The exported Home feature modules are composable fragments, not standalone
configurations. For example, `nixcfgPackages` reads the options from
`nixcfgFonts`; `nixcfgStylix` reads `nixcfgTheme` and `nixcfgFonts` and requires
Stylix's Home Manager module. `nixcfgBase` supplies shared Home defaults, the
platform module supplies platform-specific options, and downstream profile
modules can also expect Nixvim's Home Manager module.

Use `mkHome` for a complete standalone configuration, or `mkHomeModules` when
supplying your own `homeManagerConfiguration` arguments. If you import feature
modules directly instead, you must explicitly reproduce the required
`nixcfgTheme`, `nixcfgFonts`, `nixcfgBase`, matching `nixcfgDarwin`/`nixcfgLinux`,
Stylix, and Nixvim dependencies, in addition to the module arguments above.

A direct `homeManagerConfiguration` composition using `mkHomeModules` looks like
this:

```nix
{
  outputs = { home-manager, nixcfg, ... }:
    let
      system = "aarch64-darwin";
    in
    {
      homeConfigurations.alice = home-manager.lib.homeManagerConfiguration {
        pkgs = nixcfg.pkgs.${system};
        extraSpecialArgs = {
          inherit system;
          inputs = nixcfg.inputs;
          outputs = nixcfg;
          slib = nixcfg.lib;
          src = nixcfg.outPath;
          username = "alice";
        };
        modules = nixcfg.lib.mkHomeModules {
          inherit system;
          username = "alice";
          includeDefaultUserModule = false;
          extraModules = [
            nixcfg.homeModules.nixcfgPackages
            nixcfg.homeModules.nixcfgStylix
            ./home/alice.nix
          ];
        };
      };
    };
}
```

`mkHomeModules` already includes the base, theme, fonts, platform, Stylix, and
Nixvim modules, so `extraModules` only adds the opt-in feature and downstream
profile modules. Do not repeat the dependency modules there. For the shortest
supported standalone form, pass `system`, `username`, and `modules` to `mkHome`,
remove its `system` attribute, and pass the remaining attrset to
`homeManagerConfiguration`.

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
- `darwinDefaults.appearance`: `"automatic"` (default) or `"dark"`
- `darwinDefaults.spotlight.indexing`: `true` (default) leaves Spotlight alone;
  `false` turns indexing off on every volume at each activation
- `nix.gc.options`: retention for the daily garbage collection that
  `nixcfgCommon` schedules; the shared module sets none

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
