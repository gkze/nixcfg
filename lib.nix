{
  inputs,
  lib,
  outputs,
  pkgsFor,
  src,
  ...
}:
let
  inherit (builtins)
    elemAt
    fromJSON
    length
    listToAttrs
    pathExists
    readFile
    split
    ;
  inherit (lib.lists) optionals;
  inherit (lib.attrsets) optionalAttrs;

  maybePath = p: if pathExists p then p else null;
  userMetaPath = u: maybePath "${src}/home/${u}/meta.nix";
  modulesPath = "${src}/modules";
in
rec {
  inherit modulesPath;
  flakeLock = (fromJSON (readFile ./flake.lock)).nodes;
  userMetaIfExists =
    user:
    let
      userMetaFile = userMetaPath user;
    in
    optionalAttrs (userMetaFile != null) { userMeta = import userMetaFile; };
  userConfigPath = u: "${src}/home/${u}/configuration.nix";
  kernel =
    system:
    let
      sysTup = split "-" system;
    in
    elemAt sysTup (length sysTup - 1);
  homeDirBase =
    system:
    {
      darwin = "/Users";
      linux = "/home";
    }
    .${kernel system};
  srcDirBase =
    system:
    {
      darwin = "Development";
      linux = "src";
    }
    .${kernel system};
  ghRaw =
    {
      owner,
      repo,
      rev,
      path,
    }:
    "https://raw.githubusercontent.com/${owner}/${repo}/${rev}/${path}";
  mkHome =
    {
      modules ? [ ],
      system,
      username,
      ...
    }:
    {
      inherit system;
      extraSpecialArgs = {
        inherit
          inputs
          src
          system
          username
          ;
        pkgs = pkgsFor.${system};
        slib = outputs.lib;
      }
      // userMetaIfExists username;
      modules = [
        "${modulesPath}/home/base.nix"
        "${modulesPath}/home/${kernel system}.nix"
        (userConfigPath username)
      ]
      ++ modules;
    };
  mkSystem =
    {
      homeModules ? [ ],
      system,
      systemModules ? [ ],
      users ? [ ],
      ...
    }:
    let
      hmEntryPoint =
        {
          darwin = "darwin";
          linux = "nixos";
        }
        .${kernel system};
    in
    {
      inherit system;
      specialArgs = {
        inherit
          inputs
          outputs
          src
          system
          ;
        primaryUser = elemAt users 0;
      }
      // {
        slib = outputs.lib;
      };
      modules = [
        "${modulesPath}/common.nix"
        "${modulesPath}/${kernel system}/base.nix"
      ]
      ++ systemModules
      ++ optionals (length homeModules > 0) [
        inputs.home-manager."${hmEntryPoint}Modules".home-manager
        {
          home-manager = {
            useGlobalPkgs = true;
            useUserPackages = true;
            extraSpecialArgs = {
              inherit
                inputs
                outputs
                src
                system
                ;
              slib = outputs.lib;
              pkgs = pkgsFor.${system};
            };
            users = listToAttrs (
              map (user: {
                name = user;
                value = {
                  _module.args = {
                    username = user;
                  }
                  // userMetaIfExists user;
                  imports = [
                    "${modulesPath}/home/base.nix"
                    "${modulesPath}/home/${kernel system}.nix"
                    "${src}/home/${user}/configuration.nix"
                  ]
                  ++ homeModules;
                };
              }) users
            );
          };
        }
      ];
    };
}
