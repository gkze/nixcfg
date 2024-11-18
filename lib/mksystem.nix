{
  src,
  inputs,
  outputs,
  arch ? "x86_64",
  kernel ? "linux",
  users ? [ ],
  systemModules ? [ ],
  ...
}:
let
  system = "${arch}-${kernel}";

  homePath =
    {
      darwin = "/Users";
      linux = "/home";
    }
    .${kernel};

  specialArgs = {
    inherit
      inputs
      outputs
      src
      homePath
      ;
  };

  homeManagerModule =
    with inputs;
    {
      darwin = home-manager.darwinModules.home-manager;
      linux = home-manager.nixosModules.home-manager;
    }
    .${kernel};

  homeManagerConfig = {
    home-manager = {
      inherit users;
      extraSpecialArgs = specialArgs;
      sharedModules = [ "${src}/lib/base/home.nix" ];
      useGlobalPkgs = true;
      useUserPackages = true;
    };
  };

  baseSystemModule =
    {
      darwin = "${src}/lib/base/macos.nix";
      linux = "${src}/lib/base/nixos.nix";
    }
    .${kernel};

  mkSystem =
    with inputs;
    {
      darwin = nix-darwin.lib.darwinSystem;
      # have to do this in order to leverage flakelight benefits
      linux = args: args;
    }
    .${kernel};
in
mkSystem {
  inherit system specialArgs;
  modules = [
    baseSystemModule
    homeManagerModule
    homeManagerConfig
  ] ++ systemModules;
}
