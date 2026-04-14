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
      url = "github:generalaction/emdash/v0.4.32";
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
      # Temporary fork until upstream bun2nix handles 3-tuple tarball entries.
      url = "github:gkze/bun2nix?ref=fix-source-package-routing";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    crane.url = "github:ipetkov/crane";
    opencode = {
      # Temporary fork while upstream absorbs the desktop session/theme DB fixes.
      url = "github:gkze/opencode?ref=gkze/fixes";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    hermes-agent = {
      url = "github:NousResearch/hermes-agent/v2026.4.3";
      inputs = {
        nixpkgs.follows = "nixpkgs";
        pyproject-build-systems.follows = "pyproject-build-systems";
        pyproject-nix.follows = "pyproject-nix";
        uv2nix.follows = "uv2nix";
      };
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
      url = "github:erictli/scratch/v0.8.0";
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
      url = "github:jnsahaj/lumen/v2.21.0";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    worktrunk = {
      url = "github:max-sixty/worktrunk/v0.29.2";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    axiom-cli = {
      url = "github:axiomhq/cli/v0.15.0";
      flake = false;
    };
    catppuccin = {
      url = "github:catppuccin/nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    catppuccin-bat = {
      url = "github:catppuccin/bat";
      flake = false;
    };
    # Temporary fork pin until the Twilight acrylic-gap fix lands upstream.
    catppuccin-zen-browser = {
      url = "github:gkze/zen-browser?ref=fix/frappe-zen-twilight-acrylic-gap";
      flake = false;
    };
    codex = {
      url = "github:openai/codex/rust-v0.114.0";
      flake = false;
    };
    curator = {
      url = "github:gkze/curator/v0.6.0";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # gitbutler removed - using Homebrew cask (Nix build blocked by git dep issues)
    gogcli = {
      url = "github:steipete/gogcli/v0.12.0";
      flake = false;
    };
    goose-v8 = {
      url = "github:jh-block/rusty_v8/dbb64c20b9062b358b101e4592abb3ca8f646c2b";
      flake = false;
    };
    gitui-key-config = {
      url = "github:extrawurst/gitui/8876c1d0f616d55a0c0957683781fd32af815ae3";
      flake = false;
    };
    googleworkspace-cli = {
      url = "github:googleworkspace/cli";
      inputs.nixpkgs.follows = "nixpkgs";
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
      url = "github:schpet/linear-cli/v1.11.1";
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
    mux = {
      url = "github:coder/mux/v0.21.0";
      inputs.nixpkgs.follows = "nixpkgs";
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
      url = "github:batrachianai/toad/v0.6.12";
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
            inherit lib;
          };
          mkNixcfgPackage =
            pkgs:
            pkgs.callPackage ./packages/nixcfg.nix {
              inherit (self) inputs;
              outputs = self;
            };
          mkRepoCheck =
            {
              name,
              runCommandAttrs ? { },
              repoWritable ? false,
              workdir ? "src",
              setup ? "",
              command,
            }:
            {
              lib,
              pkgs,
              ...
            }@context:
            let
              resolve = value: if builtins.isFunction value then value context else value;
            in
            pkgs.runCommand name (resolve runCommandAttrs) ''
              export HOME="$TMPDIR"
              ${resolve setup}
              cp -a ${./.} src
              ${lib.optionalString repoWritable "chmod -R u+w src"}
              cd ${workdir}
              ${resolve command}
              touch $out
            '';
        in
        {
          inherit inputs;

          nixDir = ./.;

          nixDirAliases.homeConfigurations = [ "home" ];

          systems = lib.mkForce systems;

          flakelight.editorconfig = false;

          nixpkgs.config = nixpkgsConfig;

          apps.nixcfg =
            pkgs:
            let
              nixcfgPkg = mkNixcfgPackage pkgs;
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
                    excludes = lintFiles.nix.excludeGlobs;
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
                      biome-web = {
                        command = lib.getExe pkgs.biome;
                        options = [
                          "format"
                          "--config-path"
                          "biome.jsonc"
                          "--write"
                        ];
                        includes = lintFiles.biome.globs;
                        excludes = lintFiles.biome.excludeGlobs;
                      };
                      gofmt = {
                        command = lib.getExe' pkgs.go "gofmt";
                        options = [ "-w" ];
                        includes = [ "*.go" ];
                      };
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

          checks."format-repo" = lib.mkForce (
            { lib, outputs', ... }:
            ''
              ${lib.getExe outputs'.formatter} .
            ''
          );

          checks."lint-editorconfig" = mkRepoCheck {
            name = "check-lint-editorconfig";
            command =
              {
                lib,
                pkgs,
                ...
              }:
              ''
                ${lib.getExe pkgs."editorconfig-checker"} -exclude '^\.pre-commit-config\.yaml$'
              '';
          };

          checks."format-yaml-yamlfmt" = mkRepoCheck {
            name = "check-format-yaml-yamlfmt";
            command =
              {
                lib,
                pkgs,
                ...
              }:
              ''
                ${lib.getExe pkgs.yamlfmt} -lint -gitignore_excludes -conf .yamlfmt .
              '';
          };

          checks."lint-yaml-yamllint" = mkRepoCheck {
            name = "check-lint-yaml-yamllint";
            command =
              {
                lib,
                pkgs,
                ...
              }:
              ''
                ${lib.getExe pkgs.yamllint} -c .yamllint .
              '';
          };

          checks."format-web-biome" = mkRepoCheck {
            name = "check-format-web-biome";
            command =
              {
                lib,
                pkgs,
                ...
              }:
              ''
                ${lib.getExe pkgs.biome} check --config-path biome.jsonc --diagnostic-level=error .
              '';
          };

          checks."format-python-ruff" = mkRepoCheck {
            name = "check-format-python-ruff";
            setup = ''
              export RUFF_CACHE_DIR="$TMPDIR/.ruff_cache"
            '';
            command =
              {
                lib,
                pkgs,
                ...
              }:
              ''
                ${lib.getExe pkgs.ruff} format --check --config pyproject.toml .
              '';
          };

          checks."lint-python-ruff" = mkRepoCheck {
            name = "check-lint-python-ruff";
            setup = ''
              export RUFF_CACHE_DIR="$TMPDIR/.ruff_cache"
            '';
            command =
              {
                lib,
                pkgs,
                ...
              }:
              ''
                ${lib.getExe pkgs.ruff} check --config pyproject.toml .
              '';
          };

          checks."lint-python-ty" = mkRepoCheck {
            name = "check-lint-python-ty";
            command =
              {
                lib,
                pkgs,
                ...
              }:
              let
                nixcfgPkg = mkNixcfgPackage pkgs;
                tyPythonFlag = if nixcfgPkg != null then " --python ${nixcfgPkg}/bin/python" else "";
              in
              ''
                ${lib.getExe pkgs.ty} check${tyPythonFlag} .
              '';
          };

          checks."lint-workflows-actionlint" = mkRepoCheck {
            name = "check-lint-workflows-actionlint";
            repoWritable = true;
            command =
              {
                lib,
                pkgs,
                ...
              }:
              ''
                ${lib.getExe pkgs.git} init -q .
                ${lib.getExe pkgs.actionlint}
              '';
          };

          checks."test-python-pytest" = mkRepoCheck {
            name = "check-test-python-pytest";
            repoWritable = true;
            runCommandAttrs =
              { pkgs, ... }:
              {
                nativeBuildInputs = [
                  pkgs.cacert
                  pkgs.git
                  pkgs.nix
                ];
              };
            setup =
              { pkgs, ... }:
              let
                certFile = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
                nixConfig = "experimental-features = nix-command flakes";
              in
              ''
                export COVERAGE_FILE="$TMPDIR/.coverage"
                export CURL_CA_BUNDLE="${certFile}"
                export GIT_SSL_CAINFO="${certFile}"
                export NIX_CONFIG="${nixConfig}"
                export NIXCFG_NIXPKGS_PATH="${inputs.nixpkgs}"
                export NIX_SSL_CERT_FILE="${certFile}"
                export REQUESTS_CA_BUNDLE="${certFile}"
                export SSL_CERT_FILE="${certFile}"
              '';
            command =
              { pkgs, ... }:
              let
                nixcfgPkg = mkNixcfgPackage pkgs;
              in
              ''
                ${nixcfgPkg}/bin/coverage run -m pytest
                ${nixcfgPkg}/bin/coverage report
              '';
          };

          checks."verify-workflow-artifacts-refresh" = mkRepoCheck {
            name = "check-verify-workflow-artifacts-refresh";
            command =
              { pkgs, ... }:
              let
                nixcfgPkg = mkNixcfgPackage pkgs;
              in
              ''
                ${nixcfgPkg}/bin/nixcfg ci workflow verify-artifacts
              '';
          };

          checks."verify-workflow-artifacts-certify" = mkRepoCheck {
            name = "check-verify-workflow-artifacts-certify";
            command =
              { pkgs, ... }:
              let
                nixcfgPkg = mkNixcfgPackage pkgs;
              in
              ''
                ${nixcfgPkg}/bin/nixcfg ci workflow verify-artifacts --workflow .github/workflows/update-certify.yml
              '';
          };

          checks."verify-workflow-structure-refresh" = mkRepoCheck {
            name = "check-verify-workflow-structure-refresh";
            command =
              { pkgs, ... }:
              let
                nixcfgPkg = mkNixcfgPackage pkgs;
              in
              ''
                ${nixcfgPkg}/bin/nixcfg ci workflow verify-structure
              '';
          };

          checks."verify-workflow-structure-certify" = mkRepoCheck {
            name = "check-verify-workflow-structure-certify";
            command =
              { pkgs, ... }:
              let
                nixcfgPkg = mkNixcfgPackage pkgs;
              in
              ''
                ${nixcfgPkg}/bin/nixcfg ci workflow verify-structure --workflow .github/workflows/update-certify.yml
              '';
          };

        }
      );

      overlayDefault =
        final: prev:
        let
          resolvedSystem = prev.system or (final.system or "x86_64-linux");
        in
        baseOutputs.overlays.default final (prev // { system = resolvedSystem; });
    in
    (builtins.removeAttrs baseOutputs [
      "checks"
      "legacyPackages"
    ])
    // {
      checks = builtins.mapAttrs (
        _: systemChecks: builtins.removeAttrs systemChecks [ "formatting" ]
      ) baseOutputs.checks;
      pkgs = baseOutputs.legacyPackages;
      interactivePkgs = baseOutputs.legacyPackages;

      overlays = baseOutputs.overlays // {
        default = overlayDefault;
      };
    };
}
