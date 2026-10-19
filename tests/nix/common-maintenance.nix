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
    {
      system,
      extraModules ? [ ],
    }:
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
        ]
        ++ extraModules;
      };
    in
    {
      inherit (evaluated.config.nix) gc optimise;
      settings = builtins.intersectAttrs {
        max-free = null;
        min-free = null;
      } evaluated.config.nix.settings;
    };

  partialHeadroom = builtins.tryEval (
    builtins.deepSeq (maintenanceFor {
      system = "aarch64-darwin";
      extraModules = [
        {
          nixcfg.common.nix.minFreeStoreBytes = 1;
        }
      ];
    }) true
  );

  reversedHeadroom = builtins.tryEval (
    builtins.deepSeq (maintenanceFor {
      system = "aarch64-darwin";
      extraModules = [
        {
          nixcfg.common.nix = {
            minFreeStoreBytes = 2;
            maxFreeStoreBytes = 1;
          };
        }
      ];
    }) true
  );

  checks = [
    (assertEq "Darwin maintenance uses native schedules"
      {
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
        settings = { };
      }
      (maintenanceFor {
        system = "aarch64-darwin";
      })
    )
    (assertEq "Linux maintenance uses native schedules"
      {
        gc = {
          automatic = true;
          dates = "09:30";
          options = "--delete-older-than 3d";
        };
        optimise = {
          automatic = true;
          dates = "Sun 04:15";
        };
        settings = { };
      }
      (maintenanceFor {
        system = "x86_64-linux";
      })
    )
    (assertEq "George's Darwin cache policy opts into store headroom"
      {
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
        settings = {
          max-free = 137438953472;
          min-free = 34359738368;
        };
      }
      (maintenanceFor {
        system = "aarch64-darwin";
        extraModules = [ (src + "/modules/darwin/george/caches.nix") ];
      })
    )
    (assertEq "store headroom thresholds must be configured together" false partialHeadroom.success)
    (assertEq "maximum store headroom must exceed the minimum" false reversedHeadroom.success)
  ];
in
# Source/AST checks cannot prove the dynamic .${kernel} branch selects only the native schedule shape.
builtins.deepSeq checks true
