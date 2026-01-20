{
  description = "Universe";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    nixos-hardware.url = "github:NixOS/nixos-hardware/master";
    nix-homebrew.url = "github:Yeradon/nix-homebrew";
    neovim-nightly-overlay = {
      url = "github:nix-community/neovim-nightly-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-darwin = {
      url = "github:nix-darwin/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-rosetta-builder = {
      url = "github:cpick/nix-rosetta-builder";
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
    firefox = {
      url = "github:nix-community/flake-firefox-nightly";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    git-hooks = {
      url = "github:cachix/git-hooks.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    mac-app-util = {
      url = "github:hraban/mac-app-util";
      # TODO: re-enable once SBCL on Darwin is fixed
      # gitlab.common-lisp.net returns HTML (bot protection) instead of tar.gz
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixvim = {
      url = "github:nix-community/nixvim";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    opencode = {
      url = "github:anomalyco/opencode";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs = {
        pyproject-nix.follows = "pyproject-nix";
        uv2nix.follows = "uv2nix";
        nixpkgs.follows = "nixpkgs";
      };
    };
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
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
    sentry-cli = {
      url = "github:getsentry/sentry-cli/3.1.0";
      flake = false;
    };
    sops-nix = {
      url = "github:Mic92/sops-nix";
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
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    axiom-cli = {
      url = "github:axiomhq/cli/v0.14.7";
      flake = false;
    };
    beads = {
      url = "github:steveyegge/beads/v0.47.1";
      flake = false;
    };
    bufresize-nvim = {
      url = "github:kwkarlwang/bufresize.nvim";
      flake = false;
    };
    catppuccin-bat = {
      url = "github:catppuccin/bat";
      flake = false;
    };
    codex = {
      url = "github:openai/codex/rust-v0.86.0";
      flake = false;
    };
    curator = {
      url = "github:gkze/curator/v0.1.6";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    crush = {
      url = "github:charmbracelet/crush/v0.33.2";
      flake = false;
    };
    gemini-cli = {
      url = "github:google-gemini/gemini-cli/v0.24.0";
      flake = false;
    };
    gitbutler = {
      url = "github:gitbutlerapp/gitbutler/release/0.18.3";
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
    tclint = {
      url = "github:nmoroze/tclint";
      flake = false;
    };
    nix-manipulator = {
      url = "github:hoh/nix-manipulator/0.1.3";
      flake = false;
    };
    toad = {
      url = "github:batrachianai/toad/v0.5.32";
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
    zed = {
      url = "github:zed-industries/zed";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    zsh-system-clipboard = {
      url = "github:kutsan/zsh-system-clipboard";
      flake = false;
    };
    zsh-completions = {
      url = "github:zsh-users/zsh-completions";
      flake = false;
    };
    # ghostty-shaders = {
    #   url = "github:0xhckr/ghostty-shaders";
    #   flake = false;
    # };
  };

  outputs =
    {
      self,
      flakelight,
      flakelight-darwin,
      devshell,
      git-hooks,
      treefmt-nix,
      ...
    }@inputs:
    flakelight ./. (
      { lib, ... }:
      {
        inherit inputs;

        nixDir = ./.;

        nixDirAliases.homeConfigurations = [ "home" ];

        systems = lib.mkForce [
          "aarch64-darwin"
          "x86_64-linux"
        ];

        nixpkgs.config.allowUnfree = true;

        imports = [ flakelight-darwin.flakelightModules.default ];

        homeConfigurations.george = import ./home/george { outputs = self; };

        withOverlays = [
          devshell.overlays.default
          inputs.curator.overlays.default
          inputs.neovim-nightly-overlay.overlays.default
          inputs.red.overlays.default
          self.overlays.default
          (
            _: prev:
            let
              inherit (inputs.pyproject-nix.lib) scripts;
              script = scripts.loadScript {
                name = "update";
                script = ./update.py;
              };
            in
            {
              update-script = prev.writeScriptBin script.name (
                scripts.renderWithPackages {
                  inherit script;
                  python = prev.python313;
                }
              );
            }
          )
        ];

        devShell =
          pkgs:
          let
            ruffFiles = "(\\.py$|home/george/bin/git-ignore)";
            shFiles = "(\\.envrc|misc/zsh-plugins/.*\\.zsh)";
            pre-commit-check = git-hooks.lib.${pkgs.system}.run {
              src = ./.;
              package = pkgs.prek;
              hooks = {
                # Nix
                nixfmt.enable = true;
                deadnix.enable = true;
                statix.enable = true;
                # Python
                ruff = {
                  enable = true;
                  files = ruffFiles;
                };
                ruff-format = {
                  enable = true;
                  files = ruffFiles;
                };
                # Shell
                shellcheck = {
                  enable = true;
                  files = shFiles;
                  excludes = [ "misc/zsh-plugins/go\\.plugin\\.zsh" ];
                };
                shfmt = {
                  enable = true;
                  files = shFiles;
                  excludes = [ "misc/zsh-plugins/go\\.plugin\\.zsh" ];
                };
                # Markdown
                mdformat.enable = true;
                # General
                check-merge-conflicts.enable = true;
                end-of-file-fixer.enable = true;
                trim-trailing-whitespace.enable = true;
              };
            };
            editorWorkspace = inputs.uv2nix.lib.workspace.loadWorkspace {
              workspaceRoot = ./.;
            };
            editorPython = pkgs.python313;
            editorPySet =
              (pkgs.callPackage inputs.pyproject-nix.build.packages {
                python = editorPython;
              }).overrideScope
                (
                  lib.composeManyExtensions [
                    inputs.pyproject-build-systems.overlays.default
                    (editorWorkspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
                  ]
                );
            editorVenv = editorPySet.mkVirtualEnv "nixcfg-editor" { aiohttp = [ ]; };
          in
          pkgs.devshell.mkShell {
            name = "nixcfg";
            packages =
              with pkgs;
              [
                home-manager
                nh
                nil
                nix-init
                nix-manipulator
                nixos-generators
                nurl
                prek
                sops
              ]
              ++ lib.optional pkgs.stdenv.isLinux dconf2nix
              ++ pre-commit-check.enabledPackages
              ++ [ editorVenv ];
            devshell.startup.pre-commit.text = pre-commit-check.shellHook;
            devshell.startup.editor-venv.text = ''
              if [ -e .venv ] && [ ! -L .venv ]; then
                rm -rf .venv
              fi
              ln -sfn ${editorVenv} .venv
            '';
          };

        formatter =
          pkgs:
          with treefmt-nix.lib;
          let
            ruffInclude = [
              "*.py"
              "home/george/bin/git-ignore"
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
                    excludes = [ "misc/zsh-plugins/go.plugin.zsh" ];
                  };
                  shfmt = {
                    enable = true;
                    includes = shInclude;
                    excludes = [ "misc/zsh-plugins/go.plugin.zsh" ];
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
