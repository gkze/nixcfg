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
    emdash = {
      url = "github:generalaction/emdash/v0.4.24";
      flake = false;
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
      url = "github:gkze/opencode?ref=feat/added_UI_themes";
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
      url = "github:max-sixty/worktrunk/v0.28.1";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    axiom-cli = {
      url = "github:axiomhq/cli/v0.14.8";
      flake = false;
    };
    catppuccin-bat = {
      url = "github:catppuccin/bat";
      flake = false;
    };
    codex = {
      url = "github:openai/codex/rust-v0.106.0";
      flake = false;
    };
    curator = {
      url = "github:gkze/curator/v0.3.7";
      inputs.nixpkgs.follows = "nixpkgs";
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
    linearis = {
      url = "github:czottmann/linearis/main";
      flake = false;
    };
    macfuse = {
      url = "github:macfuse/library";
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
    superset = {
      url = "github:superset-sh/superset/main";
      flake = false;
    };
    nix-manipulator = {
      url = "github:hoh/nix-manipulator/0.1.3";
      flake = false;
    };
    toad = {
      url = "github:batrachianai/toad/v0.6.5";
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
    let
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];

      nixpkgsConfig = {
        allowUnfree = true;
        # Allow Google Chrome regardless of insecure status - we pin the
        # version ourselves via overlays/google-chrome/sources.json and the
        # update pipeline, so nixpkgs marking a release as insecure should
        # not block builds. Using a pname predicate avoids the brittle
        # version-string coupling that permittedInsecurePackages requires.
        allowInsecurePredicate = pkg: (pkg.pname or "") == "google-chrome";
      };

      overlayList = [
        devshell.overlays.default
        inputs.bun2nix.overlays.default
        inputs.curator.overlays.default
        inputs.lumen.overlays.default
        inputs.neovim-nightly-overlay.overlays.default
        inputs.rust-overlay.overlays.default
        (
          final: prev:
          let
            hostSystemEval = builtins.tryEval prev.stdenv.hostPlatform.system;
            hostSystem =
              if hostSystemEval.success then hostSystemEval.value else prev.system or (final.system or null);
          in
          prev.lib.optionalAttrs (hostSystem != null && builtins.hasAttr hostSystem inputs.red.packages) {
            red-reddit-cli = inputs.red.packages.${hostSystem}.default;
          }
        )
        self.overlays.default
      ];
      baseOutputs = flakelight ./. (
        { lib, ... }:
        let
          exports = import ./lib/exports.nix { src = ./.; };
          lintFiles = import ./lib/lint-files.nix;
          mkDevShell = import ./lib/dev-shell.nix {
            src = ./.;
            gitHooks = git-hooks;
            inherit
              lib
              lintFiles
              ;
          };
        in
        {
          inherit inputs;

          nixDir = ./.;

          nixDirAliases.homeConfigurations = [ "home" ];

          systems = lib.mkForce systems;

          nixpkgs.config = nixpkgsConfig;

          apps.nixcfg =
            pkgs:
            let
              nixcfgPkg = pkgs.callPackage ./packages/nixcfg.nix {
                inherit (self) inputs;
                outputs = self;
              };
            in
            {
              program = "${nixcfgPkg}/bin/nixcfg";
              meta.description = "Unified CLI for nixcfg project tasks.";
            };

          imports = [ flakelight-darwin.flakelightModules.default ];

          homeConfigurations.george = import ./home/george { outputs = self; };

          # Public module API for consuming this repo as a framework.
          inherit (exports)
            darwinModules
            homeModules
            nixosModules
            ;

          withOverlays = overlayList;

          legacyPackages = pkgs: pkgs;

          devShell = mkDevShell;

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
                    taplo = {
                      enable = true;
                      includes = lintFiles.toml.globs;
                    };
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
                      shfmt.options = [
                        "-i"
                        "2"
                        "-s"
                      ];
                      # treefmt/taplo normally auto-discovers `.taplo.toml`, but
                      # this keeps `nix fmt` stable from subdirectories.
                      taplo.options = [
                        "--config"
                        ".taplo.toml"
                      ];
                      yamlfmt.options = [
                        "-conf"
                        ".yamlfmt"
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
            {
              lib,
              pkgs,
              ...
            }:
            let
              nixcfgScript = pkgs.callPackage ./packages/nixcfg.nix {
                inherit (self) inputs;
                outputs = self;
              };
              tyPythonFlag = if nixcfgScript != null then " --python ${nixcfgScript}/bin/python" else "";
            in
            pkgs.runCommand "check-python" { } ''
              export HOME="$TMPDIR"
              export RUFF_CACHE_DIR="$TMPDIR/.ruff_cache"
              cp -a ${./.} src
              cd src
              ${lib.getExe pkgs.ruff} check --config pyproject.toml .
              ${lib.getExe pkgs.ty} check${tyPythonFlag} .
              touch $out
            '';

        }
      );

      overlayDefault =
        final: prev:
        let
          resolvedSystem = prev.system or (final.system or "x86_64-linux");
        in
        baseOutputs.overlays.default final (prev // { system = resolvedSystem; });
    in
    baseOutputs
    // {
      overlays = baseOutputs.overlays // {
        default = overlayDefault;
      };
    };
}
