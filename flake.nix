{
  description = "Universe";

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

    # Neovim configured with Nix
    nixvim = {
      url = "github:nix-community/nixvim";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Nix editor
    nix-editor = {
      url = "github:vlinkz/nix-editor";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # NixOS image creation
    nixos-generators = {
      url = "github:nix-community/nixos-generators";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Declarative disk partitioning
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = inputs:
    let
      # Grab some builtins into our lexical scope
      inherit (builtins) elemAt listToAttrs split;

      # Main user
      username = "george";

      # Shorthand for dprint WASM plugin URLs
      dprintWasmPluginUrl = n: v: "https://plugins.dprint.dev/${n}-${v}.wasm";

      # One function to declare both NixOS and Darwin system config
      mkSystem = import ./lib/mksystem.nix;
    in
    inputs.fp.lib.mkFlake { inherit inputs; } {
      # All officially supported systems
      systems = inputs.nixpkgs.lib.systems.flakeExposed;

      # Attributes here have systeme above suffixed across them
      perSystem = { system, config, pkgs, lib, ... }:
        let
          # Cross-platform configuration rebuild action
          rebuild = {
            type = "app";
            program = {
              "darwin" = inputs.nix-darwin.packages.${system}.darwin-rebuild
                + "/bin/darwin-rebuild";
              "linux" = pkgs.nixos-rebuild + "/bin/nixos-rebuild";
            }.${elemAt (split "-" system) 2};
          };
        in
        {
          # Inject Nixpkgs with our config
          # https://nixos.org/manual/nixos/unstable/options#opt-_module.args
          _module.args.pkgs = import inputs.nixpkgs {
            inherit system;
            overlays = [ inputs.devshell.overlays.default ];
            config.allowUnfree = true;
          };

          # Unified source formatting
          formatter = inputs.treefmt-nix.lib.mkWrapper pkgs {
            projectRootFile = "flake.nix";
            programs = {
              # Python
              black.enable = true;
              # Nix
              nixpkgs-fmt.enable = true;
              deadnix.enable = true;
              # Shell
              shellcheck.enable = true;
              shfmt.enable = true;
              # Lua
              stylua.enable = true;
              # JSON, Markdown
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

          # Development shell
          devShells.default = pkgs.devshell.mkShell {
            name = "nixcfg";
            packages =
              (with inputs; [
                nix-editor.packages.${system}.default
                nixos-generators.packages.${system}.default
              ])
              ++ (with pkgs; [ nix-init nix-melt nurl ]);
          };

          # For `nix run`
          apps = {
            # Long-form `nix run .#rebuild -- --flake . switch`
            rebuild = rebuild;
            # Short-form `nix run`
            default = rebuild // {
              program = pkgs.writeShellScriptBin "rebuild" ''
                ${rebuild.program} --flake . switch
              '';
            };
          };

          # NixOS installer ISO for Basis ThinkPad X1 Carbon
          # Only runs on an x86_64 system
          packages.frontier-iso = inputs.nixos-generators.nixosGenerate {
            inherit system;
            format = "install-iso";
            specialArgs = {
              hostPlatform = "x86_64-linux";
              users = [ username ];
            };
            modules = [
              ./nix/common.nix
              # NixOS requires this
              # https://search.nixos.org/options?channel=23.05&show=users.users.%3Cname%3E.isNormalUser
              (listToAttrs (map
                (u: {
                  name = "users";
                  value = { users.${u}.isNormalUser = true; };
                }) [ username ]))
            ];
          };
        };

      # System-independent (sort of) attributes. They're required to be
      # top-level without a suffixed system attribute, but ironically define
      # system-specific machine configuration.
      flake = {
        # Personal MacBook Pro
        darwinConfigurations.rocinante = mkSystem inputs {
          device = "apple-macbook-pro-m1-16in";
          arch = "aarch64";
          kernel = "darwin";
          users = [ username ];
        };


        # Basis ThinkPad X1 Carbon
        nixosConfigurations.frontier = mkSystem inputs {
          device = "lenovo-thinkpad-x1-carbon-gen10";
          arch = "x86_64";
          kernel = "linux";
          users = [ username ];
        };
      };
    };
}
