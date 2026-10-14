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
