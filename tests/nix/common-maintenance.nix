{
  lib,
  src ? ../..,
}:
let
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  maintenanceFor =
    system:
    let
      evaluated = lib.evalModules {
        specialArgs = {
          inherit system;
          hostname = null;
          inputs = { };
          slib = null;
          pkgs = {
            stdenv.hostPlatform = { inherit system; };
            nixVersions.git = "/nix/store/test-nix";
          };
        };
        modules = [
          (src + "/modules/common.nix")
          (
            { lib, ... }:
            {
              _module.freeformType = lib.types.attrsOf lib.types.anything;
            }
          )
        ];
      };
    in
    {
      inherit (evaluated.config.nix) gc optimise;
    };

  checks = [
    (assertEq "Darwin maintenance uses native schedules" {
      gc = {
        automatic = true;
        interval = {
          Hour = 9;
          Minute = 30;
        };
        options = "--delete-older-than 3d";
      };
      optimise = {
        automatic = true;
        interval = {
          Weekday = 7;
          Hour = 4;
          Minute = 15;
        };
      };
    } (maintenanceFor "aarch64-darwin"))
    (assertEq "Linux maintenance uses native schedules" {
      gc = {
        automatic = true;
        dates = "09:30";
        options = "--delete-older-than 3d";
      };
      optimise = {
        automatic = true;
        dates = "Sun 04:15";
      };
    } (maintenanceFor "x86_64-linux"))
  ];
in
# Source/AST checks cannot prove the dynamic .${kernel} branch selects only the native schedule shape.
builtins.deepSeq checks true
