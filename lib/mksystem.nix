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
      system
      ;
  };

  homeManagerModule = inputs.home-manager.nixosModules.home-manager;

  homeManagerConfig = {
    home-manager = {
      inherit users;
      extraSpecialArgs = specialArgs;
      sharedModules = [ "${src}/lib/base/home.nix" ];
      useGlobalPkgs = true;
      useUserPackages = true;
    };
  };

  baseSystemModule = "${src}/lib/base/nixos.nix";

  mkSystem = args: args;
in
mkSystem {
  inherit system specialArgs;
  modules = [
    baseSystemModule
    homeManagerModule
    homeManagerConfig
  ] ++ systemModules;
}
