{
  description = "System configuration";

  inputs = {
    # Use latest nixpkgs
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-23.05";

    # Flake helper utilities
    flake-utils.url = "github:numtide/flake-utils";

    # Manage macOS configuration
    darwin = {
      url = "github:LnL7/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Manage $HOME environment
    home-manager = {
      url = "github:nix-community/home-manager/release-23.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Unified polyglot source formatter
    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Nix development shell helper
    devshell = {
      url = "github:numtide/devshell";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { self
    , nixpkgs
    , flake-utils
    , darwin
    , home-manager
    , treefmt-nix
    , devshell
    }:
    let
      # macOS configuration helper
      darwinSystem =
        { users
        , profiles ? [ ]
        , system ? "aarch64-darwin"
        , extraModules ? [ ]
        }:
        darwin.lib.darwinSystem {
          inherit system; specialArgs = { inherit users profiles; };
          modules = [
            # nix-darwin configuration
            ./nix/configuration.nix
            # macOS system defaults
            ./nix/macos-defaults.nix
            # home-manager configuration
            home-manager.darwinModules.home-manager
            {
              home-manager = {
                useGlobalPkgs = true;
                useUserPackages = true;
                users = builtins.listToAttrs (map
                  (user: { name = user; value = import ./nix/home.nix; })
                  users);
              };
            }
          ] ++ extraModules;
        };
    in
    flake-utils.lib.eachDefaultSystem
      (defaultSystem:
      let
        pkgs = import nixpkgs {
          system = defaultSystem;
          overlays = [ devshell.overlays.default ];
        };

        dprintWasmPluginUrl = name: version:
          "https://plugins.dprint.dev/${name}-${version}.wasm";
      in
      {
        # Configure source formatters for project
        formatter = treefmt-nix.lib.mkWrapper pkgs {
          projectRootFile = "flake.nix";
          programs = {
            black.enable = true;
            # no-lambda-pattern-names is needed to preserve self input arg
            deadnix = { enable = true; no-lambda-pattern-names = true; };
            nixpkgs-fmt.enable = true;
            shellcheck.enable = true;
            shfmt.enable = true;
            stylua.enable = true;
            dprint = {
              enable = true;
              config = {
                includes = [ "**/*.{json,md}" ];
                excludes = [ "flake.lock" ];
                plugins = [
                  (dprintWasmPluginUrl "json" "0.17.3")
                  (dprintWasmPluginUrl "markdown" "0.15.3")
                ];
              };
            };
          };
        };

        # Configure Nix deveopment shell
        devShells.default = pkgs.devshell.mkShell { };
      })
    //
    {
      # macOS machines
      darwinConfigurations = {
        # Personal MacBook Pro
        rocinante = darwinSystem { users = [ "george" ]; };

        # Plaid MacBook Pro
        gkontridze-mbp = darwinSystem {
          users = [ "gkontridze" ];
          profiles = [ "plaid" ];
        };
      };
    };
}
