{
  description = "Universe";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    nixos-hardware.url = "github:NixOS/nixos-hardware/master";

    flakelight = {
      url = "github:nix-community/flakelight";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    flakelight-darwin = {
      url = "github:cmacrae/flakelight-darwin";
      inputs.flakelight.follows = "flakelight";
    };

    mac-app-util = {
      url = "github:hraban/mac-app-util";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    home-manager = {
      url = "github:nix-community/home-manager/master";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    devshell = {
      url = "github:numtide/devshell";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    nh = {
      url = "github:viperML/nh/master";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    nixvim = {
      url = "github:nix-community/nixvim";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    stylix = {
      url = "github:danth/stylix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    firefox = {
      url = "github:nix-community/flake-firefox-nightly";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    nix-homebrew = {
      url = "github:zhaofengli-wip/nix-homebrew";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    homebrew-core = {
      url = "github:homebrew/homebrew-core";
      flake = false;
    };

    homebrew-cask = {
      url = "github:homebrew/homebrew-cask";
      flake = false;
    };

    homebrew-bundle = {
      url = "github:homebrew/homebrew-bundle";
      flake = false;
    };

    bufresize-nvim = {
      url = "github:kwkarlwang/bufresize.nvim";
      flake = false;
    };

    treewalker-nvim = {
      url = "github:aaronik/treewalker.nvim";
      flake = false;
    };

    catppuccin-delta = {
      url = "github:catppuccin/delta";
      flake = false;
    };

    kdl-vim = {
      url = "github:imsnif/kdl.vim";
      flake = false;
    };

    zsh-system-clipboard = {
      url = "github:kutsan/zsh-system-clipboard";
      flake = false;
    };
  };

  outputs =
    {
      self,
      flakelight,
      flakelight-darwin,
      devshell,
      treefmt-nix,
      ...
    }@inputs:
    flakelight ./. (
      { lib, ... }:
      {
        inherit inputs;

        nixDir = ./.;
        systems = lib.mkForce [
          "aarch64-darwin"
          "x86_64-linux"
        ];
        nixpkgs.config.allowUnfree = true;
        imports = [ flakelight-darwin.flakelightModules.default ];

        withOverlays = [
          devshell.overlays.default
          self.overlays.default
        ];

        devShell =
          pkgs:
          pkgs.devshell.mkShell {
            name = "nixcfg";
            packages =
              with pkgs;
              [
                home-manager
                nh
                nix-init
                nixos-generators
                nurl
              ]
              ++ lib.lists.optional pkgs.stdenv.isLinux dconf2nix;
          };

        formatter =
          pkgs:
          with treefmt-nix.lib;
          let
            shInclude = [
              ".envrc"
              "misc/yabai-center"
            ];
            inherit
              (evalModule pkgs {
                projectRootFile = "flake.nix";
                programs = {
                  mdformat.enable = true;
                  nixfmt.enable = true;
                  deadnix.enable = true;
                  statix.enable = true;
                  ruff-check.enable = true;
                  ruff-format.enable = true;
                  shellcheck = {
                    enable = true;
                    includes = shInclude;
                  };
                  shfmt = {
                    enable = true;
                    includes = shInclude;
                  };
                };
              })
              config
              ;
          in
          mkWrapper pkgs (
            config
            // {
              build.wrapper = pkgs.writeShellScriptBin "treefmt-nix" ''
                exec ${config.build.wrapper}/bin/treefmt --no-cache "$@"
              '';
            }
          );

        checks.formatting = lib.mkForce (
          { lib, outputs', ... }:
          ''
            ${lib.getExe outputs'.formatter} .
          ''
        );
      }
    );
}
