{
  config,
  lib,
  ...
}:
let
  inherit (lib)
    concatMap
    mkIf
    mkOption
    types
    unique
    ;

  cfg = config.nixcfg.topgrade;

  packageLabel = package: package.name or "<unknown package>";

  # A Nix-managed CLI cannot update itself, so Topgrade's step for it always
  # fails. The package names the steps that target it, which keeps the
  # knowledge next to the derivation instead of in a hand-maintained list.
  stepsOf =
    package:
    package.passthru.topgrade.steps or (throw ''
      nixcfg.topgrade.excludedPackages: ${packageLabel package} does not declare
      passthru.topgrade.steps. Add that attribute to the derivation, or list the
      Topgrade step name in nixcfg.topgrade.disabledSteps instead.
    '');
in
{
  options.nixcfg.topgrade = {
    disabledSteps = mkOption {
      type = types.listOf types.str;
      default = [ ];
      example = [ "home_manager" ];
      description = ''
        Topgrade step names to disable because Nix manages the tool. Use this for
        packages this repository does not own, which cannot carry
        `passthru.topgrade.steps`.
      '';
    };

    excludedPackages = mkOption {
      type = types.listOf types.package;
      default = [ ];
      description = ''
        Packages to exclude from Topgrade updates. Each one must declare
        `passthru.topgrade.steps` listing the Topgrade steps that target it.
      '';
    };
  };

  config = mkIf (cfg.disabledSteps != [ ] || cfg.excludedPackages != [ ]) {
    programs.topgrade.settings.misc.disable = unique (
      cfg.disabledSteps ++ concatMap stepsOf cfg.excludedPackages
    );
  };
}
