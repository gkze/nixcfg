# Unifying NixOS and Darwin system config declaration...
{ inputs, arch, kernel, users, extraModules ? [ ] }:
let
  hostPlatform = "${arch}-${kernel}";

  sysFn = {
    linux = inputs.nixpkgs.lib.nixosSystem;
    darwin = inputs.nix-darwin.lib.darwinSystem;
  }.${kernel};

  hmMod = {
    linux = inputs.home-manager.nixosModules.home-manager;
    darwin = inputs.home-manager.darwinModules.home-manager;
  }.${kernel};
in
sysFn {
  specialArgs = { inherit users hostPlatform; };
  modules =
    [ ./common.nix ]
    ++ (inputs.nixpkgs.lib.optionals (kernel == "darwin") [
      ./macos-defaults.nix
      ./homebrew.nix
    ])
    ++ [
      hmMod
      {
        home-manager = {
          useGlobalPkgs = true;
          useUserPackages = true;
          extraSpecialArgs = { inherit hostPlatform; };
          users = builtins.listToAttrs
            (map (u: { name = u; value = import ./${u}.nix; }) users);
        };
      }
    ]
    ++ extraModules;
}
