{
  description = "Universe";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    nixpkgs-neovim-0-10-3.url = "github:NixOS/nixpkgs/21808d22b1cda1898b71cf1a1beb524a97add2c4";
    nixos-hardware.url = "github:NixOS/nixos-hardware/master";
    nix-darwin = {
      url = "github:LnL7/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    flakelight = {
      url = "github:nix-community/flakelight";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    flakelight-darwin = {
      url = "github:cmacrae/flakelight-darwin";
      inputs = {
        flakelight.follows = "flakelight";
        nix-darwin.follows = "nix-darwin";
      };
    };
    devshell = {
      url = "github:numtide/devshell";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    blink-cmp = {
      url = "github:Saghen/blink.cmp/main";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    firefox = {
      url = "github:nix-community/flake-firefox-nightly";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    home-manager = {
      url = "github:nix-community/home-manager/master";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    mac-app-util = {
      url = "github:hraban/mac-app-util";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nh = {
      url = "github:viperML/nh/master";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-homebrew = {
      url = "github:zhaofengli-wip/nix-homebrew";
      inputs = {
        # TODO: remove after https://github.com/zhaofengli/nix-homebrew/pull/71
        # lands
        brew-src.url = "github:Homebrew/brew/4.4.25";
        nixpkgs.follows = "nixpkgs";
      };
    };
    nixvim = {
      url = "github:nix-community/nixvim";
      inputs.nixpkgs.follows = "nixpkgs-neovim-0-10-3";
    };
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    stylix = {
      url = "github:danth/stylix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    bufresize-nvim = {
      url = "github:kwkarlwang/bufresize.nvim";
      flake = false;
    };
    catppuccin-bat = {
      url = "github:catppuccin/bat";
      flake = false;
    };
    catppuccin-delta = {
      url = "github:catppuccin/delta";
      flake = false;
    };
    homebrew-cask = {
      url = "github:homebrew/homebrew-cask";
      flake = false;
    };
    homebrew-core = {
      url = "github:homebrew/homebrew-core";
      flake = false;
    };
    kdl-vim = {
      url = "github:imsnif/kdl.vim";
      flake = false;
    };
    mdq = {
      url = "github:yshavit/mdq";
      flake = false;
    };
    naersk = {
      url = "github:nix-community/naersk";
      flake = false;
    };
    sublime-kdl = {
      url = "github:eugenesvk/sublime-kdl/2.0.2";
      flake = false;
    };
    stars = {
      url = "github:gkze/gh-stars/v0.19.24";
      flake = false;
    };
    trdsql = {
      url = "github:noborus/trdsql/v1.1.0";
      flake = false;
    };
    treewalker-nvim = {
      url = "github:aaronik/treewalker.nvim";
      flake = false;
    };
    turbo = {
      url = "github:vercel/turbo/v2.4.5-canary.4";
      flake = false;
    };
    vim-bundle-mako = {
      url = "github:sophacles/vim-bundle-mako";
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
            ruffInclude = map (p: "home/george/${p}") [
              "bin/git-ignore"
              "ptpython.py"
            ];
            shInclude = [
              ".envrc"
              "misc/zsh-plugins/*.zsh"
            ];
            inherit
              (evalModule pkgs {
                projectRootFile = "flake.nix";
                programs = {
                  mdformat.enable = true;
                  nixfmt.enable = true;
                  deadnix.enable = true;
                  statix.enable = true;
                  ruff-check = {
                    enable = true;
                    includes = ruffInclude;
                  };
                  ruff-format = {
                    enable = true;
                    includes = ruffInclude;
                  };
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
                exec ${lib.getExe config.build.wrapper} --no-cache "$@"
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
