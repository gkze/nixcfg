{
  description = "System configuration";

  inputs = {
    # Use latest nixpkgs
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # Flake helper
    fp.url = "github:hercules-ci/flake-parts";

    # Manage macOS configuration
    nix-darwin = {
      url = "github:LnL7/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Manage $HOME environment
    home-manager = {
      url = "github:nix-community/home-manager";
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

    # Nix editor
    nix-editor = {
      url = "github:vlinkz/nix-editor";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = inputs:
    let
      # Main user
      username = "george";

      # Helper to shorten writing dprint WASM plugin URLs
      dprintWasmPluginUrl = n: v: "https://plugins.dprint.dev/${n}-${v}.wasm";

      # Unifying NixOS and Darwin system config declaration...
      mkSystem = { arch, kernel, users, extraModules ? [ ] }:
        let
          hostPlatform = "${arch}-${kernel}";

          sysFn = {
            "linux" = inputs.nixpkgs.lib.nixosSystem;
            "darwin" = inputs.nix-darwin.lib.darwinSystem;
          }.${kernel};

          hmMod = {
            "linux" = inputs.home-manager.nixosModules.home-manager;
            "darwin" = inputs.home-manager.darwinModules.home-manager;
          }.${kernel};
        in
        sysFn {
          specialArgs = { inherit users hostPlatform; };
          modules =
            [ ./nix/common.nix ]
            ++ (inputs.nixpkgs.lib.optionals (kernel == "darwin") [
              ./nix/macos-defaults.nix
              ./nix/homebrew.nix
            ])
            ++ [
              hmMod
              {
                home-manager = {
                  useGlobalPkgs = true;
                  useUserPackages = true;
                  users = builtins.listToAttrs
                    (map (u: { name = u; value = import ./nix/${u}.nix; }) users);
                };
              }
            ]
            ++ extraModules;
        };
    in
    inputs.fp.lib.mkFlake { inherit inputs; } {
      # All officially supported systems
      systems = inputs.nixpkgs.lib.systems.flakeExposed;

      # Attributes here have systeme above suffixed across them
      perSystem = { system, config, pkgs, ... }: {
        # https://nixos.org/manual/nixos/unstable/options#opt-_module.args
        _module.args.pkgs = import inputs.nixpkgs {
          inherit system;
          overlays = [ inputs.devshell.overlays.default ];
          config.allowUnfree = true;
        };

        formatter = inputs.treefmt-nix.lib.mkWrapper pkgs {
          projectRootFile = "flake.nix";
          programs = {
            black.enable = true;
            nixpkgs-fmt.enable = true;
            shellcheck.enable = true;
            shfmt.enable = true;
            stylua.enable = true;
            # no-lambda-pattern-names is needed to preserve self input arg
            deadnix = { enable = true; no-lambda-pattern-names = true; };
            dprint = {
              enable = true;
              settings = {
                includes = [ "**/*.{json,md}" ];
                excludes = [ "flake.lock" ];
                plugins = [
                  (dprintWasmPluginUrl "json" "0.19.0")
                  (dprintWasmPluginUrl "markdown" "0.16.2")
                ];
              };
            };
          };
        };

        devShells.default = pkgs.devshell.mkShell {
          name = "nixcfg";
          packages = [
            inputs.nix-editor.packages.${system}.default
            pkgs.nix-init
            pkgs.nix-melt
          ];
        };

        apps.rebuild = {
          type = "app";
          program = {
            "darwin" = inputs.nix-darwin.packages.${system}.darwin-rebuild
              + "/bin/darwin-rebuild";
            "linux" = pkgs.nixos-rebuild + "/bin/nixos-rebuild";
          }.${builtins.elemAt (builtins.split "-" system) 2};
        };
      };

      # System-independent (sort of) attributes. They're required to be
      # top-level without a suffixed system attribute, but ironically define
      # system-specific machine configuration.
      flake = {
        # Personal MacBook Pro
        darwinConfigurations.rocinante = mkSystem {
          arch = "aarch64";
          kernel = "darwin";
          users = [ username ];
        };

        # Basis ThinkPad X1 Carbon
        nixosConfigurations.frontier = mkSystem {
          arch = "x86_64";
          kernel = "linux";
          users = [ username ];
        };
      };
    };
}
