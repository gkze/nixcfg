# Unifying NixOS and Darwin system config declaration...
# TODO: configure hostname
inputs: { device ? "", arch, kernel, users, sysMods ? [ ], hmMods ? [ ], ... }@args:
let
  inherit (builtins) listToAttrs pathExists;
  inherit (inputs.nixpkgs.lib.attrsets) optionalAttrs;
  inherit (inputs.nixpkgs.lib) optionals;

  # Construct host platform tuple from CPU architecture and OS kernel
  hostPlatform = "${arch}-${kernel}";

  # Select the appropriate system configuration function
  sysFn = {
    linux = inputs.nixpkgs.lib.nixosSystem;
    darwin = inputs.nix-darwin.lib.darwinSystem;
  }.${kernel};

  # Select the appropriate Home Manager module
  hmMod = {
    linux = inputs.home-manager.nixosModules.home-manager;
    darwin = inputs.home-manager.darwinModules.home-manager;
  }.${kernel};
in
sysFn {
  # Pass additional arguments to all modules below
  specialArgs = { inherit users hostPlatform inputs; };
  modules =
    # Common to everything and everyone
    [ ../nix/common.nix ]
    # Device-specific system module
    ++ optionals
      (device != null && pathExists ../device/system/${device}.nix)
      [ ../device/system/${device}.nix ]
    # OS-specific
    ++ {
      darwin = [
        # Base macOS configuration
        ../os/macos/default.nix
        # The highly valuable but unfortunately under-documented macOS defaults
        # https://macos-defaults.com/
        ../os/macos/macos-defaults.nix
        # Homebrew - could be extended to be made usable on Linux
        ../nix/homebrew.nix
      ];
      linux = [ ../os/nixos.nix ]; # NixOS-specific configuration
    }.${kernel}
    # Needed for configuration to apply correctly. Hint: /var/empty error
    ++ [{
      users.users = listToAttrs (map
        (user: {
          name = user;
          value = ({
            name = user;
            # Set home directory
            home = {
              darwin = "/Users/${user}";
              linux = "/home/${user}";
            }.${kernel};
          }
          //
          # Add system configuration for user if it exists
          optionalAttrs (pathExists ../user/${user}/system.nix)
            (import ../user/${user}/system.nix { inherit inputs kernel user; })
          );
        })
        users);
    }]
    # User / home environment
    ++ [
      hmMod
      {
        home-manager = {
          useGlobalPkgs = true;
          useUserPackages = true;
          extraSpecialArgs = {
            inherit hostPlatform inputs args;
            hmMods =
              # Device-specific Home Manager module
              optionals
                (device != null && pathExists ../device/home/${device}.nix)
                [ ../device/home/${device}.nix ]
              ++ {
                darwin = [ ];
                linux = [ ];
              }.${kernel}
              # Any additional Home Manager modules passed
              ++ hmMods;
          };
          users = listToAttrs (map
            (user: {
              name = user;
              value = import ../user/${user}/home.nix;
            })
            users);
        };
      }
    ]
    # Any additional system moduels passed
    ++ sysMods;
}

