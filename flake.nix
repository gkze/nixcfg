{
  description = "Universe";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    # Pinned nixpkgs with working Swift build (before clang-21.1.8 broke it)
    # Tracking: https://github.com/NixOS/nixpkgs/issues/483584
    nixpkgs-swift.url = "github:NixOS/nixpkgs/70801e06d9730c4f1704fbd3bbf5b8e11c03a2a7";
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
    flake-edit = {
      url = "github:a-kenji/flake-edit";
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
    bun2nix = {
      url = "github:nix-community/bun2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    crane.url = "github:ipetkov/crane";
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
    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    scratch = {
      url = "github:erictli/scratch/v0.7.1";
      flake = false;
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
    lumen = {
      url = "github:jnsahaj/lumen/v2.20.0";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    worktrunk = {
      url = "github:max-sixty/worktrunk/v0.27.0";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    axiom-cli = {
      url = "github:axiomhq/cli/v0.14.8";
      flake = false;
    };
    beads = {
      url = "github:steveyegge/beads/v0.56.1";
      flake = false;
    };
    catppuccin-bat = {
      url = "github:catppuccin/bat";
      flake = false;
    };
    codex = {
      url = "github:openai/codex/rust-v0.104.0";
      flake = false;
    };
    curator = {
      url = "github:gkze/curator/v0.3.4";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    crush = {
      url = "github:charmbracelet/crush/v0.44.0";
      flake = false;
    };
    gemini-cli = {
      url = "github:google-gemini/gemini-cli/v0.29.7";
      flake = false;
    };
    # gitbutler removed - using Homebrew cask (Nix build blocked by git dep issues)
    gogcli = {
      url = "github:steipete/gogcli/v0.11.0";
      flake = false;
    };
    gitui-key-config = {
      url = "github:extrawurst/gitui/8876c1d0f616d55a0c0957683781fd32af815ae3";
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
    linear-cli = {
      url = "github:schpet/linear-cli/v1.10.0";
      flake = false;
    };
    macfuse = {
      url = "github:macfuse/library";
      flake = false;
    };
    mdformat = {
      url = "github:hukkin/mdformat/1.0.0";
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
    pantsbuild-tap = {
      url = "github:pantsbuild/homebrew-tap";
      flake = false;
    };
    sublime-kdl = {
      url = "github:eugenesvk/sublime-kdl/2.0.5";
      flake = false;
    };
    nix-manipulator = {
      url = "github:hoh/nix-manipulator/0.1.3";
      flake = false;
    };
    toad = {
      url = "github:batrachianai/toad/v6.0.2";
      flake = false;
    };
    treesitter-textobjects = {
      url = "github:gkze/nvim-treesitter-textobjects/feat/nix-expand-textobjects";
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
      let
        # ─── Lint file patterns ───────────────────────────────────────────
        # Defined once, referenced in both devShell (git-hooks, regex)
        # and formatter (treefmt-nix, globs).
        lintFiles = {
          ruff = {
            regex = "(\\.(py|pyi)$|home/george/bin/git-ignore)";
            globs = [
              "*.py"
              "*.pyi"
              "home/george/bin/git-ignore"
            ];
          };
          shell = {
            regex = "(\\.envrc|misc/zsh-plugins/.*\\.zsh)";
            globs = [
              ".envrc"
              "misc/zsh-plugins/*.zsh"
            ];
            excludeRegex = [ "misc/zsh-plugins/go\\.plugin\\.zsh" ];
            excludeGlobs = [ "misc/zsh-plugins/go.plugin.zsh" ];
          };
        };
      in
      {
        inherit inputs;

        nixDir = ./.;

        nixDirAliases.homeConfigurations = [ "home" ];

        systems = lib.mkForce [
          "aarch64-darwin"
          "aarch64-linux"
          "x86_64-linux"
        ];

        nixpkgs.config = {
          allowUnfree = true;
          # Allow Google Chrome regardless of insecure status — we pin the
          # version ourselves via overlays/google-chrome/sources.json and the
          # update pipeline, so nixpkgs marking a release as insecure should
          # not block builds.  Using a pname predicate avoids the brittle
          # version-string coupling that permittedInsecurePackages requires.
          allowInsecurePredicate = pkg: (pkg.pname or "") == "google-chrome";
        };

        apps.nixcfg =
          { nixcfg-script, ... }:
          {
            program = "${nixcfg-script}/bin/nixcfg";
            meta.description = "Unified CLI for nixcfg project tasks.";
          };

        imports = [ flakelight-darwin.flakelightModules.default ];

        homeConfigurations.george = import ./home/george { outputs = self; };

        # Public module API for consuming this repo as a framework.
        nixosModules = {
          nixcfgCommon = ./modules/common.nix;
          nixcfgBase = ./modules/nixos/base.nix;
          nixcfgProfiles = ./modules/nixos/profiles.nix;
        };

        darwinModules = {
          nixcfgCommon = ./modules/common.nix;
          nixcfgBase = ./modules/darwin/base.nix;
          nixcfgProfiles = ./modules/darwin/profiles.nix;
          nixcfgHomebrew = ./modules/darwin/homebrew.nix;
        };

        homeModules = {
          nixcfgBase = ./modules/home/base.nix;
          nixcfgGit = ./modules/home/git.nix;
          nixcfgProfiles = ./modules/home/profiles.nix;
          nixcfgPackages = ./modules/home/packages.nix;
          nixcfgOpencode = ./modules/home/opencode.nix;
          nixcfgTheme = ./modules/home/theme.nix;
          nixcfgFonts = ./modules/home/fonts.nix;
          nixcfgStylix = ./modules/home/stylix.nix;
          nixcfgZsh = ./modules/home/zsh.nix;
          nixcfgDarwin = ./modules/home/darwin.nix;
          nixcfgLinux = ./modules/home/linux.nix;
          nixcfgLanguageBun = ./modules/home/languages/bun.nix;
          nixcfgLanguageGo = ./modules/home/languages/go.nix;
          nixcfgLanguagePython = ./modules/home/languages/python.nix;
          nixcfgLanguageRust = ./modules/home/languages/rust.nix;
        };

        withOverlays = [
          devshell.overlays.default
          inputs.bun2nix.overlays.default
          inputs.curator.overlays.default
          inputs.lumen.overlays.default
          inputs.neovim-nightly-overlay.overlays.default
          inputs.red.overlays.default
          inputs.rust-overlay.overlays.default
          self.overlays.default
        ];

        devShell =
          pkgs:
          let
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
                  files = lintFiles.ruff.regex;
                  entry = "${lib.getExe pkgs.ruff} check --config pyproject.toml .";
                  pass_filenames = false;
                  always_run = true;
                };
                ruff-format = {
                  enable = true;
                  files = lintFiles.ruff.regex;
                };
                ty = {
                  enable = true;
                  files = lintFiles.ruff.regex;
                  package = pkgs.ty;
                  entry = "${lib.getExe pkgs.ty} check .";
                  pass_filenames = false;
                  always_run = true;
                };
                # Shell
                shellcheck = {
                  enable = true;
                  files = lintFiles.shell.regex;
                  excludes = lintFiles.shell.excludeRegex;
                };
                shfmt = {
                  enable = true;
                  files = lintFiles.shell.regex;
                  excludes = lintFiles.shell.excludeRegex;
                  settings.simplify = false; # let .editorconfig control style
                };
                # Markdown
                mdformat = {
                  enable = true;
                  package = pkgs.mdformat;
                };
                # YAML
                yamlfmt.enable = true;
                # General
                check-merge-conflicts.enable = true;
                end-of-file-fixer.enable = true;
                trim-trailing-whitespace.enable = true;
              };
            };
          in
          pkgs.devshell.mkShell {
            name = "nixcfg";

            packages =
              with pkgs;
              [
                flake-edit
                nh
                nil
                nix-init
                nix-manipulator
                nixos-generators
                nurl
                prek
                sops
                yamlfmt
              ]
              ++ lib.optional pkgs.stdenv.isLinux dconf2nix
              ++ pre-commit-check.enabledPackages;

            devshell.startup = {
              pre-commit.text = pre-commit-check.shellHook;
            };
          };

        formatter =
          pkgs:
          with treefmt-nix.lib;
          let
            inherit
              (evalModule pkgs {
                projectRootFile = "flake.nix";
                programs = {
                  nixfmt.enable = true;
                  deadnix.enable = true;
                  statix.enable = true;
                  ruff-check = {
                    enable = true;
                    includes = lintFiles.ruff.globs;
                  };
                  ruff-format = {
                    enable = true;
                    includes = lintFiles.ruff.globs;
                  };
                  shellcheck = {
                    enable = true;
                    includes = lintFiles.shell.globs;
                    excludes = lintFiles.shell.excludeGlobs;
                  };
                  shfmt = {
                    enable = true;
                    includes = lintFiles.shell.globs;
                    excludes = lintFiles.shell.excludeGlobs;
                  };
                  yamlfmt.enable = true;
                };
                # treefmt/ruff normally auto-discovers `pyproject.toml`, but being
                # explicit keeps `nix fmt` aligned with uv-managed Ruff config,
                # even when invoked from subdirectories.
                settings = {
                  formatter = {
                    ruff-check.options = [
                      "--config"
                      "pyproject.toml"
                      "--fix-only"
                    ];
                    ruff-format.options = [
                      "--config"
                      "pyproject.toml"
                    ];
                    "markdown-table-formatter" = {
                      command = lib.getExe' (pkgs.python3.withPackages (
                        ps: with ps; [
                          mdformat
                          mdformat-tables
                        ]
                      )) "mdformat";
                      includes = [ "*.md" ];
                    };
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

        checks.python =
          { lib, pkgs, ... }:
          pkgs.runCommand "check-python" { } ''
            export HOME="$TMPDIR"
            export RUFF_CACHE_DIR="$TMPDIR/.ruff_cache"
            cp -a ${./.} src
            cd src
            ${lib.getExe pkgs.ruff} check --config pyproject.toml .
            ${lib.getExe pkgs.ty} check .
            touch $out
          '';
      }
    );
}
