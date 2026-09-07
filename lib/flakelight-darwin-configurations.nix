# Adapted from flakelight-darwin's darwinConfigurations.nix.
# Copyright (C) 2024 Calum MacRae <hi@cmacr.ae>
# SPDX-License-Identifier: MIT
# The full MIT permission notice is in ../LICENSE.
#
# Keep check names independent of host evaluation. Raw constructors already
# declare their system; only check values need the evaluated configuration.
{
  config,
  lib,
  inputs,
  flakelight,
  moduleArgs,
  ...
}:
let
  configurationSystem = import ./configuration-system.nix;
  isEvaluated = configuration: configuration ? config.system.build.toplevel;
  mkDarwin =
    hostname: cfg:
    inputs.nix-darwin.lib.darwinSystem (
      cfg
      // {
        specialArgs = {
          inherit inputs hostname;
          inputs' = builtins.mapAttrs (_: flakelight.selectAttr cfg.system) inputs;
        }
        // (cfg.specialArgs or { });
        modules = [ config.propagationModule ] ++ (cfg.modules or [ ]);
      }
    );
  configurations = builtins.mapAttrs (
    hostname: cfg: if isEvaluated cfg then cfg else mkDarwin hostname cfg
  ) config.darwinConfigurations;
in
{
  options.darwinConfigurations = lib.mkOption {
    type = flakelight.types.optCallWith moduleArgs (
      lib.types.lazyAttrsOf (flakelight.types.optCallWith moduleArgs lib.types.attrs)
    );
    default = { };
  };

  config = {
    outputs = lib.mkIf (config.darwinConfigurations != { }) {
      darwinConfigurations = configurations;
      checks = lib.foldl lib.recursiveUpdate { } (
        lib.mapAttrsToList (name: cfg: {
          ${configurationSystem cfg}."darwin-${name}" =
            let
              configuration = configurations.${name};
            in
            configuration.pkgs.runCommand "check-darwin-${name}" { }
              "echo ${configuration.config.system.build.toplevel} > $out";
        }) config.darwinConfigurations
      );
    };
    nixDirAliases.darwinConfigurations = [ "darwin" ];
  };
}
