{
  lib,
  pkgs,
  src ? ../..,
}:
let
  inherit (import ./lib.nix) assertEq;

  # Packages stand in for the real derivations; only the metadata matters here.
  packageWithSteps =
    name: steps:
    pkgs.runCommand name {
      passthru = {
        topgrade.steps = steps;
      };
    } "touch $out";
  packageWithoutSteps = pkgs.runCommand "no-topgrade-metadata" { } "touch $out";

  evalDisable =
    settings:
    (lib.evalModules {
      modules = [
        {
          options.programs.topgrade.settings.misc.disable = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [ ];
          };
        }
        (import (src + "/modules/home/topgrade.nix"))
        settings
      ];
    }).config.programs.topgrade.settings.misc.disable;
in
lib.all lib.id [
  (assertEq "no exclusions leaves Topgrade untouched" [ ] (evalDisable { }))
  (assertEq "explicit steps pass through" [ "home_manager" ] (evalDisable {
    nixcfg.topgrade.disabledSteps = [ "home_manager" ];
  }))
  (assertEq "packages contribute the steps they declare" [ "hermes_agent" ] (evalDisable {
    nixcfg.topgrade.excludedPackages = [ (packageWithSteps "hermes-desktop" [ "hermes_agent" ]) ];
  }))
  (assertEq "explicit steps and package steps combine and dedupe"
    [
      "codex"
      "hermes_agent"
    ]
    (evalDisable {
      nixcfg.topgrade.disabledSteps = [ "codex" ];
      nixcfg.topgrade.excludedPackages = [
        (packageWithSteps "chatgpt" [ "codex" ])
        (packageWithSteps "hermes-desktop" [ "hermes_agent" ])
      ];
    })
  )
  (assertEq "a package without step metadata fails loudly" false
    (builtins.tryEval (
      builtins.deepSeq (evalDisable {
        nixcfg.topgrade.excludedPackages = [ packageWithoutSteps ];
      }) true
    )).success
  )
]
