{
  description = "Universe";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    nixos-hardware.url = "github:NixOS/nixos-hardware/master";
    nix-homebrew.url = "github:zhaofengli-wip/nix-homebrew";
    neovim-nightly-overlay = {
      url = "github:nix-community/neovim-nightly-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-darwin = {
      url = "github:nix-darwin/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    flakelight = {
      url = "github:nix-community/flakelight";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    flakelight-darwin = {
      url = "github:gkze/flakelight-darwin";
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
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    mac-app-util = {
      url = "github:hraban/mac-app-util";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixvim = {
      url = "github:nix-community/nixvim";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    red = {
      url = "github:gkze/red";
      inputs.nixpkgs.follows = "nixpkgs";
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
    macfuse = {
      url = "github:macfuse/library";
      flake = false;
    };
    markdown-table-formatter = {
      url = "github:nvuillam/markdown-table-formatter";
      flake = false;
    };
    mdq = {
      url = "github:yshavit/mdq";
      flake = false;
    };
    mountpoint-s3 = {
      url = "github:awslabs/mountpoint-s3";
      flake = false;
    };
    naersk = {
      url = "github:nix-community/naersk";
      flake = false;
    };
    pantsbuild-tap = {
      url = "github:pantsbuild/homebrew-tap";
      flake = false;
    };
    sublime-kdl = {
      url = "github:eugenesvk/sublime-kdl/2.0.6";
      flake = false;
    };
    stars = {
      url = "github:gkze/gh-stars/v0.19.24";
      flake = false;
    };
    treewalker-nvim = {
      url = "github:aaronik/treewalker.nvim";
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
          self.overlays.default
          devshell.overlays.default
          inputs.neovim-nightly-overlay.overlays.default
          inputs.red.overlays.default
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
                nil
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
                settings.formatter."markdown-table-formatter" = with pkgs; {
                  command = "${
                    python3.withPackages (
                      ps: with ps; [
                        mdformat
                        mdformat-tables
                      ]
                    )
                  }/bin/mdformat";
                  includes = [ "*.md" ];
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
