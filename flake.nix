{
  description = "Universe";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    # Pinned nixpkgs with working Swift build (before clang-21.1.8 broke it)
    # Tracking: https://github.com/NixOS/nixpkgs/issues/483584
    nixpkgs-swift.url = "github:NixOS/nixpkgs/70801e06d9730c4f1704fbd3bbf5b8e11c03a2a7";
    nix-homebrew = {
      url = "github:Yeradon/nix-homebrew";
      # Keep brew-src, homebrew-cask, and homebrew-core pinned as one tested
      # tuple; current tap syntax can require matching Homebrew/Ruby support.
      inputs.brew-src.url = "github:Homebrew/brew/fb60c879bac6e441c1e71468c0d0887d4c430558";
    };
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
      url = "github:generalaction/emdash/v1.1.39";
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
    gitbutler = {
      url = "github:gitbutlerapp/gitbutler/release/0.21.0";
      flake = false;
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
    nh = {
      # v4.3.x captures and drops Darwin activation logs even with
      # --show-activation-logs; keep the last release before that change.
      url = "github:nix-community/nh/v4.2.0";
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
      url = "github:NousResearch/hermes-agent/v2026.7.7.2";
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
      url = "github:erictli/scratch/v0.10.0";
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
      url = "github:jnsahaj/lumen/v2.30.0";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    worktrunk = {
      url = "github:max-sixty/worktrunk/v0.67.0";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    axiom-cli = {
      url = "github:axiomhq/cli/v0.16.0";
      flake = false;
    };
    anthropic-cli = {
      url = "github:anthropics/anthropic-cli/v1.17.0";
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
      url = "github:openai/codex/rust-v0.144.4";
      flake = false;
    };
    curator = {
      url = "github:gkze/curator/v0.7.2";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    gogcli = {
      url = "github:steipete/gogcli/v0.34.0";
      flake = false;
    };
    openai-cli = {
      url = "github:openai/openai-cli/v1.4.0";
      flake = false;
    };
    github-desktop = {
      type = "git";
      url = "https://github.com/desktop/desktop.git";
      ref = "refs/tags/release-3.6.2";
      submodules = true;
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
      # Update with nix-homebrew.inputs.brew-src and homebrew-core.
      url = "github:homebrew/homebrew-cask/b40e0b0c4faa6b0d7e458b67bb96820621411bde";
      flake = false;
    };
    homebrew-core = {
      # Update with nix-homebrew.inputs.brew-src and homebrew-cask.
      url = "github:homebrew/homebrew-core/d1b066427e859ac2820238300a3a49fa2880fe1b";
      flake = false;
    };
    hwatch = {
      url = "github:blacknon/hwatch/0.4.2";
      flake = false;
    };
    linear-cli = {
      url = "github:schpet/linear-cli/v2.1.0";
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
      url = "github:coder/mux/v0.28.0";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    mountpoint-s3 = {
      url = "github:awslabs/mountpoint-s3";
      flake = false;
    };
    t3code = {
      url = "github:pingdotgg/t3code/main";
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
      url = "github:batrachianai/toad/v0.6.20";
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
        # Allow selected pinned binary runtimes regardless of insecure status.
        # We pin and cache them ourselves, so nixpkgs marking a release as
        # insecure should not block builds. Use pname/version predicates to
        # avoid broad allowlists while keeping package-specific pins explicit.
        allowInsecurePredicate =
          pkg:
          let
            pname = pkg.pname or "";
            version = pkg.version or "";
          in
          pname == "google-chrome"
          || (
            pname == "electron"
            && builtins.elem version [
              "38.7.2"
              "40.9.3"
            ]
          );
      };

      overlayList = [
        devshell.overlays.default
        inputs.bun2nix.overlays.default
        inputs.curator.overlays.default
        inputs.lumen.overlays.default
        inputs.neovim-nightly-overlay.overlays.default
        inputs.rust-overlay.overlays.default
        inputs.nh.overlays.default
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
          inherit (lintFiles.python)
            compilePaths
            pyupgradePaths
            pythonScriptPaths
            ruffMutationExcludes
            ;
          pythonScriptFindPredicates = lib.concatMapStringsSep " " (
            path: "-o -path './${path}'"
          ) pythonScriptPaths;
          oxfmtPatterns = lintFiles.oxfmt.globs ++ map (glob: "!${glob}") lintFiles.oxfmt.excludeGlobs;
          mkDevShell = import ./lib/dev-shell.nix {
            src = ./.;
            gitHooks = git-hooks;
            inherit lib lintFiles mkNixcfgPackage;
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
          # Evaluate the nixcfg package once per system and share it across
          # every check below instead of re-deriving it per check.
          nixcfgPackages = lib.genAttrs systems (
            system: mkNixcfgPackage baseOutputs.legacyPackages.${system}
          );
          # Repo checks, keyed by check name. Each spec is a mkRepoCheck
          # argument set; `nixcfg = true` additionally passes the shared
          # `nixcfgPkg`/`nixcfgVenv` for that system into the command.
          mkNamedRepoCheck =
            checkName: spec:
            mkRepoCheck (
              builtins.removeAttrs spec [ "nixcfg" ]
              // {
                name = "check-${checkName}";
                command =
                  if spec.nixcfg or false then
                    context:
                    let
                      nixcfgPkg = nixcfgPackages.${context.pkgs.stdenv.hostPlatform.system};
                    in
                    spec.command (
                      context
                      // {
                        inherit nixcfgPkg;
                        nixcfgVenv = nixcfgPkg.passthru.venv;
                      }
                    )
                  else
                    spec.command;
              }
            );
          repoCheckSpecs = {
            "lint-editorconfig" = {
              command =
                { lib, pkgs, ... }:
                ''
                  ${lib.getExe pkgs."editorconfig-checker"} -exclude '^\.pre-commit-config\.yaml$'
                '';
            };

            "format-yaml-yamlfmt" = {
              command =
                { lib, pkgs, ... }:
                ''
                  ${lib.getExe pkgs.yamlfmt} -lint -gitignore_excludes -conf .yamlfmt .
                '';
            };

            "lint-yaml-yamllint" = {
              command =
                { lib, pkgs, ... }:
                ''
                  ${lib.getExe pkgs.yamllint} -c .yamllint .
                '';
            };

            "format-web-oxfmt" = {
              command =
                { lib, pkgs, ... }:
                ''
                  ${lib.getExe pkgs.oxfmt} --check --config .oxfmtrc.json --no-error-on-unmatched-pattern ${lib.escapeShellArgs oxfmtPatterns}
                '';
            };

            "lint-web-oxlint" = {
              command =
                { lib, pkgs, ... }:
                ''
                  OXLINT_TSGOLINT_PATH=${lib.getExe pkgs.tsgolint} ${lib.getExe pkgs.oxlint} --config .oxlintrc.json --type-aware --quiet .
                '';
            };

            "format-python-pyupgrade" = {
              repoWritable = true;
              nixcfg = true;
              command =
                {
                  lib,
                  pkgs,
                  nixcfgVenv,
                  ...
                }:
                ''
                  ${lib.getExe pkgs.git} init -q .
                  ${lib.getExe pkgs.git} add -A
                  find . \
                    \( -path './.direnv' -o -path './.git' -o -path './.pytest_cache' -o -path './.ruff_cache' -o -path './.venv' -o -path './node_modules' -o -path './result' -o -name '_generated.py' \) -prune -o \
                    -type f \
                    \( -name '*.py' -o -name '*.pyi' ${pythonScriptFindPredicates} \) \
                    -print0 \
                    | ${pkgs.findutils}/bin/xargs -0 -r ${nixcfgVenv}/bin/python -m lib.fix_python_multi_except --pyupgrade-exe ${nixcfgVenv}/bin/pyupgrade --pyupgrade-arg=--py313-plus
                  ${lib.getExe pkgs.git} diff --exit-code -- .
                '';
            };

            "lint-python-compile" = {
              nixcfg = true;
              command =
                { lib, nixcfgVenv, ... }:
                ''
                  ${nixcfgVenv}/bin/python ${./lib/check_python_compile.py} ${lib.escapeShellArgs compilePaths}
                '';
            };

            "format-python-ruff" = {
              nixcfg = true;
              setup = ''
                export RUFF_CACHE_DIR="$TMPDIR/.ruff_cache"
              '';
              command =
                { nixcfgVenv, ... }:
                ''
                  ${nixcfgVenv}/bin/ruff format --check --config pyproject.toml .
                '';
            };

            "lint-python-ruff" = {
              nixcfg = true;
              setup = ''
                export RUFF_CACHE_DIR="$TMPDIR/.ruff_cache"
              '';
              command =
                { nixcfgVenv, ... }:
                ''
                  ${nixcfgVenv}/bin/ruff check --config pyproject.toml .
                '';
            };

            "lint-python-ty" = {
              nixcfg = true;
              command =
                { nixcfgVenv, ... }:
                ''
                  ${nixcfgVenv}/bin/ty check --python ${nixcfgVenv}/bin/python .
                '';
            };

            "lint-workflows-actionlint" = {
              repoWritable = true;
              command =
                { lib, pkgs, ... }:
                ''
                  ${lib.getExe pkgs.git} init -q .
                  ${pkgs.findutils}/bin/find .github/workflows -maxdepth 1 -type f ! -name '*.lock.yml' \( -name '*.yml' -o -name '*.yaml' \) -print0 \
                    | ${pkgs.findutils}/bin/xargs -0 ${lib.getExe pkgs.actionlint}
                '';
            };

            "test-python-pytest" = {
              repoWritable = true;
              nixcfg = true;
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
                { pkgs, nixcfgVenv, ... }:
                ''
                  ${lib.getExe pkgs.git} init -q .
                  ${lib.getExe pkgs.git} add -A .
                  ${nixcfgVenv}/bin/coverage run -m pytest
                  ${nixcfgVenv}/bin/coverage report
                '';
            };

            "verify-workflow-generated" = {
              nixcfg = true;
              command =
                { nixcfgPkg, ... }:
                ''
                  ${nixcfgPkg}/bin/nixcfg ci workflow verify-generated
                '';
            };
          };
        in
        {
          inherit inputs;

          nixDir = ./.;

          nixDirAliases.homeConfigurations = [ "home" ];

          systems = lib.mkForce systems;

          flakelight.editorconfig = false;

          # Export the standalone Home Manager config manually below without
          # letting flakelight wire it into per-system checks.
          disabledModules = [ "homeConfigurations.nix" ];

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
              nixcfgPkg = mkNixcfgPackage pkgs;
              nixcfgVenv = nixcfgPkg.passthru.venv;
              pythonExe = "${nixcfgVenv}/bin/python";
              pyupgradeExe = "${nixcfgVenv}/bin/pyupgrade";
              ruffExe = "${nixcfgVenv}/bin/ruff";
              textHygieneScript = ./lib/format_text.py;
              textHygieneFormat = pkgs.writeShellScriptBin "format-text-hygiene" ''
                exec ${lib.getExe pkgs.python3} ${textHygieneScript} "$@"
              '';
              # Shared shape for formatters that rewrite files in place: format
              # each file into a scratch location, then overwrite the original
              # only when the formatted result differs.
              mkFormatWrapper =
                {
                  name,
                  tmpTemplate,
                  directory ? false,
                  resultFile ? null,
                  # Shell snippet that reads "$file" and writes the formatted
                  # result to "$formatted" ("$tmp" is the scratch file or dir).
                  format,
                }:
                pkgs.writeShellScriptBin name ''
                  set -euo pipefail

                  for file in "$@"; do
                    tmp="$(${lib.getExe' pkgs.coreutils "mktemp"} ${lib.optionalString directory "-d "}"''${TMPDIR:-/tmp}/${tmpTemplate}.XXXXXX")"
                    formatted=${if directory then ''"$tmp/${resultFile}"'' else ''"$tmp"''}
                    ${format}
                    if ! ${lib.getExe' pkgs.diffutils "cmp"} -s "$formatted" "$file"; then
                      ${lib.getExe' pkgs.coreutils "cat"} "$formatted" > "$file"
                    fi
                    ${lib.getExe' pkgs.coreutils "rm"} -rf "$tmp"
                  done
                '';
              jsonlFormat = mkFormatWrapper {
                name = "format-jsonl";
                tmpTemplate = "jsonl";
                format = ''${lib.getExe pkgs.jq} -c . "$file" > "$formatted"'';
              };
              goModFormat = mkFormatWrapper {
                name = "format-go-mod";
                tmpTemplate = "go-mod";
                directory = true;
                resultFile = "go.mod";
                format = ''
                  ${lib.getExe' pkgs.coreutils "cp"} "$file" "$formatted"
                  (
                    cd "$tmp"
                    ${lib.getExe' pkgs.go "go"} mod edit -fmt go.mod
                  )
                '';
              };
              twilightAutoconfigFormat = mkFormatWrapper {
                name = "format-twilight-autoconfig";
                tmpTemplate = "twilight-autoconfig";
                format = ''
                  ${lib.getExe pkgs.oxfmt} \
                    --config .oxfmtrc.json \
                    --stdin-filepath twilight.js \
                    < "$file" > "$formatted"
                '';
              };
              inherit
                (evalModule pkgs {
                  projectRootFile = "flake.nix";
                  programs = {
                    nixfmt.enable = true;
                    deadnix.enable = true;
                    statix.enable = true;
                    oxfmt = {
                      enable = true;
                      includes = lintFiles.oxfmt.globs;
                      excludes = lintFiles.oxfmt.excludeGlobs;
                    };
                    buf = {
                      enable = true;
                      includes = lintFiles.protobuf.globs;
                    };
                    gofmt = {
                      enable = true;
                      includes = lintFiles.go.globs;
                    };
                    ruff-check = {
                      enable = true;
                      includes = lintFiles.ruff.globs;
                      excludes = ruffMutationExcludes;
                    };
                    ruff-format = {
                      enable = true;
                      includes = lintFiles.ruff.globs;
                      excludes = ruffMutationExcludes;
                    };
                    shellcheck = {
                      enable = true;
                      includes = lintFiles.shell.globs;
                    };
                    shfmt = {
                      enable = true;
                      includes = lintFiles.shell.globs;
                    };
                    yamlfmt = {
                      enable = true;
                      includes = lintFiles.yaml.globs;
                    };
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
                      # Normalize invalid multi-except rewrites first so
                      # pyupgrade and compile checks converge on valid Python 3
                      # syntax using the uv-managed nixcfg runtime. Keep the
                      # established pyupgrade floor until Python 3.14 rewrites
                      # are applied as an intentional formatting migration.
                      python-pyupgrade = {
                        command = pythonExe;
                        options = [
                          "-m"
                          "lib.fix_python_multi_except"
                          "--pyupgrade-exe"
                          pyupgradeExe
                          "--pyupgrade-arg=--py313-plus"
                          "--pyupgrade-arg=--exit-zero-even-if-changed"
                        ];
                        includes = pyupgradePaths;
                        excludes = [ "**/_generated.py" ];
                      };
                      ruff-check = {
                        command = ruffExe;
                        options = [
                          "--config"
                          "pyproject.toml"
                          "--fix-only"
                        ];
                      };
                      ruff-format = {
                        command = ruffExe;
                        options = [
                          "--config"
                          "pyproject.toml"
                        ];
                      };
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
                      oxfmt.options = lib.mkForce [
                        "--config"
                        ".oxfmtrc.json"
                        "--no-error-on-unmatched-pattern"
                      ];
                      go-mod-format = {
                        command = lib.getExe goModFormat;
                        includes = lintFiles.goMod.globs;
                      };
                      jsonl-format = {
                        command = lib.getExe jsonlFormat;
                        includes = lintFiles.jsonl.globs;
                      };
                      "markdown-table-formatter" = {
                        command = lib.getExe' (pkgs.python3.withPackages (
                          ps: with ps; [
                            mdformat
                            mdformat-tables
                          ]
                        )) "mdformat";
                        includes = lintFiles.markdown.globs;
                        excludes = lintFiles.markdown.excludeGlobs;
                      };
                      twilight-autoconfig-format = {
                        command = lib.getExe twilightAutoconfigFormat;
                        includes = lintFiles.twilightAutoconfig.globs;
                      };
                      "text-hygiene" = {
                        command = lib.getExe textHygieneFormat;
                        includes = lintFiles.text.globs;
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
                  if [ -z "''${HOME:-}" ] || [ "$HOME" = /homeless-shelter ]; then
                    export HOME="''${TMPDIR:-/tmp}/treefmt-home"
                    ${lib.getExe' pkgs.coreutils "mkdir"} -p "$HOME"
                  fi
                  export XDG_CACHE_HOME="''${XDG_CACHE_HOME:-$HOME/.cache}"
                  export XDG_CONFIG_HOME="''${XDG_CONFIG_HOME:-$HOME/.config}"
                  export XDG_DATA_HOME="''${XDG_DATA_HOME:-$HOME/.local/share}"
                  export XDG_STATE_HOME="''${XDG_STATE_HOME:-$HOME/.local/state}"
                  ${lib.getExe' pkgs.coreutils "mkdir"} -p \
                    "$XDG_CACHE_HOME" \
                    "$XDG_CONFIG_HOME" \
                    "$XDG_DATA_HOME" \
                    "$XDG_STATE_HOME"
                  exec ${lib.getExe config.build.wrapper} --no-cache "$@"
                '';
              }
            );

          checks = {
            "format-repo" = lib.mkForce (
              { lib, outputs', ... }:
              ''
                ${lib.getExe outputs'.formatter} .
              ''
            );

            "test-nix-default-api" =
              { pkgs, ... }:
              assert import ./tests/nix/default-api/default-api.nix { src = ./.; };
              pkgs.runCommand "check-test-nix-default-api" { } ''
                touch $out
              '';

            "test-nix-package-helpers" =
              { pkgs, ... }:
              assert import ./tests/nix/package-helpers.nix { src = ./.; };
              pkgs.runCommand "check-test-nix-package-helpers" { } ''
                touch $out
              '';

            "test-nix-opencode-desktop" =
              { pkgs, ... }:
              assert import ./packages/opencode-desktop/tests.nix { inherit self; };
              pkgs.runCommand "check-test-nix-opencode-desktop" { } ''
                touch $out
              '';

            "test-nix-prefetch-git-darwin-heredoc" =
              { pkgs, ... }: import ./tests/nix/nix-prefetch-git-darwin-heredoc { inherit pkgs; };

            "cache-electron-runtimes" = { pkgs, ... }: pkgs.electron-runtimes;
          }
          // builtins.mapAttrs mkNamedRepoCheck repoCheckSpecs;

        }
      );

      overlayDefault =
        final: prev:
        let
          resolvedSystem = prev.system or (final.system or "x86_64-linux");
        in
        baseOutputs.overlays.default final (prev // { system = resolvedSystem; });
      mkStandaloneHomeConfiguration =
        name: cfg:
        inputs.home-manager.lib.homeManagerConfiguration (
          (builtins.removeAttrs cfg [ "system" ])
          // {
            extraSpecialArgs = {
              inherit inputs;
              inputs' = builtins.mapAttrs (_: flakelight.selectAttr cfg.system) inputs;
            }
            // (cfg.extraSpecialArgs or { });
            modules = [
              (
                { lib, ... }:
                {
                  home.username = lib.mkDefault (builtins.head (builtins.match "([^@]*)(@.*)?" name));
                }
              )
            ]
            ++ (cfg.modules or [ ]);
            pkgs = baseOutputs.legacyPackages.${cfg.system};
          }
        );
    in
    (builtins.removeAttrs baseOutputs [
      "checks"
      "legacyPackages"
    ])
    // {
      homeConfigurations.george = mkStandaloneHomeConfiguration "george" (
        import ./home/george { outputs = self; }
      );

      checks = builtins.mapAttrs (
        _: systemChecks:
        inputs.nixpkgs.lib.filterAttrs (
          name: _: name != "formatting" && !(inputs.nixpkgs.lib.hasPrefix "home-" name)
        ) systemChecks
      ) baseOutputs.checks;
      pkgs = baseOutputs.legacyPackages;
      interactivePkgs = baseOutputs.legacyPackages;

      overlays = baseOutputs.overlays // {
        default = overlayDefault;
      };
    };
}
