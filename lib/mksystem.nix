{
  src,
  inputs,
  outputs,
  arch ? "x86_64",
  kernel ? "linux",
  hostName ? "nixos",
  users ? { },
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
      hostName
      homePath
      ;
  };
  homeManagerConfig = {
    home-manager = {
      inherit users;
      extraSpecialArgs = specialArgs;
      useGlobalPkgs = true;
      useUserPackages = true;
    };
  };
  homeManagerModule =
    with inputs;
    {
      darwin = home-manager.darwinModules.home-manager;
      linux = home-manager.nixosModules.home-manager;
    }
    .${kernel};
  baseSystemModule =
    {
      darwin = ../macos/_base.nix;
      linux = ../nixos/_base.nix;
    }
    .${kernel};
  mkSystem =
    with inputs;
    {
      darwin = nix-darwin.lib.darwinSystem;
      linux = nixpkgs.lib.nixosSystem;
    }
    .${kernel};
in
mkSystem {
  inherit system specialArgs;
  modules =
    [ baseSystemModule ]
    ++ systemModules
    ++ [
      homeManagerModule
      homeManagerConfig
    ];
}
