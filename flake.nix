{
  description = "Universe";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    # Pinned nixpkgs with working Swift build (before clang-21.1.8 broke it)
    # Tracking: https://github.com/NixOS/nixpkgs/issues/483584
    nixpkgs-swift.url = "github:NixOS/nixpkgs/70801e06d9730c4f1704fbd3bbf5b8e11c03a2a7";
    nix-homebrew = {
      url = "github:Yeradon/nix-homebrew";
      # Homebrew 5.1.15 is the minimum release with brew bundle --force-cleanup,
      # required by current nix-darwin. Keep the brew/tap pins tested as a tuple.
      inputs.brew-src.url = "github:Homebrew/brew/863696a47f8d9292cb25084b6f8228e003084101";
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
      url = "github:nix-community/flakelight-darwin";
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
      url = "github:generalaction/emdash/v1.1.40";
      flake = false;
    };
    git-hooks = {
      url = "github:cachix/git-hooks.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    gitbutler = {
      url = "github:gitbutlerapp/gitbutler/release/0.22.1";
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
      # Nixvim constructs its own package set; keep its tested Nixpkgs pin
      # instead of silently substituting the root flake's revision.
      url = "github:nix-community/nixvim";
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
      url = "github:NousResearch/hermes-agent/v2026.8.27";
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
    scratch = {
      url = "github:erictli/scratch/v1.0.0";
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
      url = "github:jnsahaj/lumen/v2.32.0";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    worktrunk = {
      url = "github:max-sixty/worktrunk/v0.75.0";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    axiom-cli = {
      url = "github:axiomhq/cli/v0.19.1";
      flake = false;
    };
    anthropic-cli = {
      url = "github:anthropics/anthropic-cli/v1.28.0";
      flake = false;
    };
    base16-schemes-src = {
      # Match nixpkgs' base16-schemes source without requiring its derivation at eval time.
      url = "github:tinted-theming/schemes/43dd14f6466a782bd57419fdfb5f398c74d6ac53";
      flake = false;
    };
    catppuccin = {
      url = "github:catppuccin/nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    catppuccin-element-src = {
      # Match catppuccin/nix's Element port without realizing its derivation at eval time.
      url = "github:catppuccin/element/f8236600302ef016c7366b96414a09e086996b71";
      flake = false;
    };
    catppuccin-bat = {
      url = "github:catppuccin/bat";
      flake = false;
    };
    catppuccin-bottom-src = {
      # Match catppuccin/nix's Bottom port without realizing its derivation at eval time.
      url = "github:catppuccin/bottom/eadd75acd0ecad4a58ade9a1d6daa3b97ccec07c";
      flake = false;
    };
    # Temporary fork pin until the Twilight acrylic-gap fix lands upstream.
    catppuccin-zen-browser = {
      url = "github:gkze/zen-browser?ref=fix/frappe-zen-twilight-acrylic-gap";
      flake = false;
    };
    codex = {
      url = "github:openai/codex/rust-v0.150.1";
      flake = false;
    };
    curator = {
      url = "github:gkze/curator/v0.7.3";
      inputs = {
        nixpkgs.follows = "nixpkgs";
        rust-overlay.follows = "curator-rust-overlay-compat";
      };
    };
    curator-rust-overlay-compat = {
      url = "path:./lib/pinned-input-platform-compat";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    gogcli = {
      url = "github:steipete/gogcli/v0.38.1";
      flake = false;
    };
    openai-cli = {
      url = "github:openai/openai-cli/v1.9.0";
      flake = false;
    };
    github-desktop = {
      type = "git";
      url = "https://github.com/desktop/desktop.git";
      ref = "refs/tags/release-3.6.4";
      submodules = true;
      flake = false;
    };
    goose = {
      type = "github";
      owner = "aaif-goose";
      repo = "goose";
      ref = "v1.48.0";
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
      # Retained tap snapshot; revalidate with brew-src changes.
      url = "github:homebrew/homebrew-cask/b40e0b0c4faa6b0d7e458b67bb96820621411bde";
      flake = false;
    };
    homebrew-core = {
      # Retained tap snapshot; revalidate with brew-src changes.
      url = "github:homebrew/homebrew-core/d1b066427e859ac2820238300a3a49fa2880fe1b";
      flake = false;
    };
    hwatch = {
      url = "github:blacknon/hwatch/0.4.2";
      flake = false;
    };
    linear-cli = {
      url = "github:schpet/linear-cli/v2.5.0";
      flake = false;
    };
    macfuse = {
      url = "github:macfuse/library";
      flake = false;
    };
    mux = {
      url = "github:coder/mux/v0.28.2";
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

      electronRuntimePolicy = builtins.fromJSON (
        builtins.readFile ./packages/electron-runtimes/versions.json
      );
      electronRuntimeVersions =
        assert electronRuntimePolicy.schemaVersion == 1;
        electronRuntimePolicy.versions;

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
          pname == "google-chrome" || (pname == "electron" && builtins.elem version electronRuntimeVersions);
      };

      overlayList = [
        devshell.overlays.default
        inputs.bun2nix.overlays.default
        inputs.curator.overlays.default
        inputs.lumen.overlays.default
        (import ./overlays/_lib/neovim-nightly-overlay.nix { inherit inputs; })
        inputs.rust-overlay.overlays.default
        inputs.nh.overlays.default
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
            pythonPyupgradeExcludes
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
          # Keep each check keyed to the files it can observe. Read-only checks
          # run from these immutable store paths; only mutation checks copy one.
          mkCheckSource =
            fileset:
            lib.fileset.toSource {
              root = ./.;
              inherit fileset;
            };
          filesWithExtensions =
            extensions:
            lib.fileset.fileFilter (file: lib.any (extension: file.hasExt extension) extensions) ./.;
          filesetFromPaths = paths: lib.fileset.unions (map (path: ./. + "/${path}") paths);
          yamlFiles = lib.fileset.fileFilter (file: file.hasExt "yaml" || file.hasExt "yml") ./.;
          yamlLintFiles = lib.fileset.difference yamlFiles (
            lib.fileset.fileFilter (file: file.hasExt "yaml") ./lib/nix/schemas
          );
          webFormatFiles = lib.fileset.difference (filesWithExtensions [
            "cjs"
            "css"
            "js"
            "json"
            "jsonc"
            "ts"
          ]) ./schemas/codegen/testdata/lockfile-golden/expected.codegen.lock.json;
          webLintFiles = filesWithExtensions [
            "cjs"
            "js"
            "ts"
          ];
          pythonFiles = lib.fileset.unions [
            (filesWithExtensions [
              "py"
              "pyi"
            ])
            (filesetFromPaths pythonScriptPaths)
          ];
          generatedPythonFiles = lib.fileset.fileFilter (
            file: (file.hasExt "py" || file.hasExt "pyi") && file.name == "_generated.py"
          ) ./.;
          pyupgradeExcludedFiles = filesetFromPaths pythonPyupgradeExcludes;
          pyupgradeFiles = lib.fileset.unions [
            ./.gitignore
            (lib.fileset.difference pythonFiles (
              lib.fileset.unions [
                generatedPythonFiles
                pyupgradeExcludedFiles
              ]
            ))
          ];
          ruffFormatFiles = lib.fileset.difference pythonFiles (filesetFromPaths ruffMutationExcludes);
          pythonToolFiles = lib.fileset.unions [
            ./.gitignore
            ./pyproject.toml
            pythonFiles
          ];
          ruffFormatToolFiles = lib.fileset.unions [
            ./.gitignore
            ./pyproject.toml
            ruffFormatFiles
          ];
          schemaVerificationFiles = lib.fileset.unions [
            ./.root
            ./pyproject.toml
            ./schema_codegen.yaml
            ./nixcfg.py
            ./lib/nix/models/_generated.py
            ./lib/schema_codegen/models/_generated.py
            (lib.fileset.fileFilter (file: file.hasExt "yaml") ./lib/nix/schemas)
            (lib.fileset.fileFilter (file: file.hasExt "json") ./schemas/codegen)
          ];
          mkRepoCheck =
            {
              name,
              runCommandAttrs ? { },
              repoWritable ? false,
              source,
              setup ? "",
              command,
            }:
            {
              pkgs,
              ...
            }@context:
            let
              resolve = value: if builtins.isFunction value then value context else value;
            in
            pkgs.runCommand name (resolve runCommandAttrs) ''
              export HOME="$TMPDIR"
              ${resolve setup}
              ${
                if source == null then
                  ""
                else if repoWritable then
                  ''
                    cp -a ${source} src
                    chmod -R u+w src
                    cd src
                  ''
                else
                  "cd ${source}"
              }
              ${resolve command}
              touch $out
            '';
          mkEvalOnlyCheck =
            checkName: test:
            {
              pkgs,
              ...
            }@context:
            assert test context;
            pkgs.runCommand "check-${checkName}" { } ''
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
              source = mkCheckSource ./.;
              command =
                { lib, pkgs, ... }:
                ''
                  ${lib.getExe pkgs."editorconfig-checker"} -exclude '^\.pre-commit-config\.yaml$'
                '';
            };

            "format-yaml-yamlfmt" = {
              source = mkCheckSource (
                lib.fileset.unions [
                  ./.gitignore
                  ./.yamlfmt
                  yamlFiles
                ]
              );
              command =
                { lib, pkgs, ... }:
                ''
                  ${lib.getExe pkgs.yamlfmt} -lint -gitignore_excludes -conf .yamlfmt .
                '';
            };

            "lint-yaml-yamllint" = {
              source = mkCheckSource (
                lib.fileset.unions [
                  ./.yamllint
                  yamlLintFiles
                ]
              );
              command =
                { lib, pkgs, ... }:
                ''
                  ${lib.getExe pkgs.yamllint} -c .yamllint .
                '';
            };

            "format-web-oxfmt" = {
              source = mkCheckSource (
                lib.fileset.unions [
                  ./.editorconfig
                  ./.oxfmtrc.json
                  ./.gitignore
                  ./flake.lock
                  webFormatFiles
                ]
              );
              command =
                { lib, pkgs, ... }:
                ''
                  ${lib.getExe pkgs.oxfmt} --check --config .oxfmtrc.json --no-error-on-unmatched-pattern ${lib.escapeShellArgs oxfmtPatterns}
                '';
            };

            "lint-web-oxlint" = {
              source = mkCheckSource (
                lib.fileset.unions [
                  ./.gitignore
                  ./.oxlintrc.json
                  webLintFiles
                ]
              );
              command =
                { lib, pkgs, ... }:
                ''
                  OXLINT_TSGOLINT_PATH=${lib.getExe pkgs.tsgolint} ${lib.getExe pkgs.oxlint} --config .oxlintrc.json --type-aware --quiet .
                '';
            };

            "format-python-pyupgrade" = {
              repoWritable = true;
              nixcfg = true;
              source = mkCheckSource pyupgradeFiles;
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
                    \( -path './.claude/worktrees' -o -path './.direnv' -o -path './.git' -o -path './.pytest_cache' -o -path './.ruff_cache' -o -path './.venv' -o -path './node_modules' -o -path './result' -o -name '_generated.py' \) -prune -o \
                    -type f \
                    \( -name '*.py' -o -name '*.pyi' ${pythonScriptFindPredicates} \) \
                    -print0 \
                    | ${pkgs.findutils}/bin/xargs -0 -r ${nixcfgVenv}/bin/pyupgrade --py314-plus
                  ${lib.getExe pkgs.git} diff --exit-code -- .
                '';
            };

            "lint-python-compile" = {
              nixcfg = true;
              source = mkCheckSource pythonFiles;
              command =
                { lib, nixcfgVenv, ... }:
                ''
                  ${nixcfgVenv}/bin/python ${./lib/check_python_compile.py} ${lib.escapeShellArgs compilePaths}
                '';
            };

            "format-python-ruff" = {
              nixcfg = true;
              source = mkCheckSource ruffFormatToolFiles;
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
              source = mkCheckSource pythonToolFiles;
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
              source = mkCheckSource pythonToolFiles;
              command =
                { nixcfgVenv, ... }:
                ''
                  ${nixcfgVenv}/bin/ty check --python ${nixcfgVenv}/bin/python .
                '';
            };

            "verify-python-generated" = {
              nixcfg = true;
              source = mkCheckSource schemaVerificationFiles;
              command =
                { nixcfgVenv, ... }:
                ''
                  ${nixcfgVenv}/bin/python ./nixcfg.py schema verify
                '';
            };

            "verify-runtime-package" = {
              nixcfg = true;
              source = null;
              command =
                { nixcfgVenv, ... }:
                ''
                  site_packages="$(${nixcfgVenv}/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
                  test ! -e "$site_packages/lib/tests"
                  test -f "$site_packages/lib/nix/py.typed"
                  test -f "$site_packages/lib/nix/schemas/_version.json"
                  test -f "$site_packages/lib/nix/schemas/store-v1.yaml"
                '';
            };

            "test-python-pytest" = {
              repoWritable = true;
              nixcfg = true;
              source = mkCheckSource ./.;
              runCommandAttrs =
                { pkgs, ... }:
                {
                  nativeBuildInputs = [
                    pkgs.bun
                    pkgs.cacert
                    pkgs.git
                    pkgs.nix
                    pkgs.nodejs
                    pkgs.rsync
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
                  mkdir -p node_modules
                  ln -s ${pkgs.typescript}/lib/node_modules/typescript node_modules/typescript
                  ${nixcfgVenv}/bin/coverage run -m pytest
                  ${nixcfgVenv}/bin/coverage report
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
                      python-pyupgrade = {
                        command = pyupgradeExe;
                        options = [
                          "--py314-plus"
                          "--exit-zero-even-if-changed"
                        ];
                        includes = pyupgradePaths;
                        excludes = [ "**/_generated.py" ] ++ pythonPyupgradeExcludes;
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
                            mdformat-gfm
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

            "test-nix-default-api" = mkEvalOnlyCheck "test-nix-default-api" (
              _: import ./tests/nix/default-api/default-api.nix { src = ./.; }
            );

            "test-nix-buzz-export-readiness" = mkEvalOnlyCheck "test-nix-buzz-export-readiness" (
              { pkgs, ... }:
              if pkgs.stdenv.hostPlatform.system == "aarch64-darwin" then
                import ./tests/nix/buzz-export-readiness.nix {
                  inherit pkgs;
                  src = ./.;
                }
              else
                true
            );

            "test-nix-crate2nix-source-slice" =
              { pkgs, ... }:
              import ./tests/nix/crate2nix-source-slice.nix {
                inherit pkgs;
                src = ./.;
              };

            "test-nix-crate-cache-boundaries" = mkEvalOnlyCheck "test-nix-crate-cache-boundaries" (
              { pkgs, ... }:
              import ./tests/nix/crate-cache-boundaries.nix {
                inherit (pkgs) lib;
                system = pkgs.stdenv.hostPlatform.system;
              }
            );

            "test-nix-codex-bundled-plugin-repair" =
              { pkgs, ... }:
              if pkgs.stdenv.hostPlatform.isDarwin then
                import ./tests/nix/codex-bundled-plugin-repair.nix {
                  inherit pkgs;
                  inherit (pkgs) lib;
                  src = ./.;
                }
              else
                pkgs.runCommand "test-codex-bundled-plugin-repair-skipped" { } ''
                  touch "$out"
                '';

            "test-nix-dock-defaults" = mkEvalOnlyCheck "test-nix-dock-defaults" (
              { pkgs, ... }:
              import ./tests/nix/dock-defaults.nix {
                inherit (pkgs) lib;
                src = ./.;
              }
            );

            "test-nix-common-maintenance" = mkEvalOnlyCheck "test-nix-common-maintenance" (
              { pkgs, ... }:
              import ./tests/nix/common-maintenance.nix {
                inherit (pkgs) lib;
                src = ./.;
              }
            );

            "test-nix-gpg-session" = mkEvalOnlyCheck "test-nix-gpg-session" (
              _: import ./tests/nix/gpg-session { inherit self; }
            );

            "test-nix-nvim-keymaps" = mkEvalOnlyCheck "test-nix-nvim-keymaps" (
              _:
              import ./tests/nix/nvim-keymaps.nix {
                config = self.homeConfigurations.george.config;
                src = ./.;
              }
            );

            "test-nix-opencode-desktop" = mkEvalOnlyCheck "test-nix-opencode-desktop" (
              { pkgs, ... }:
              import ./packages/opencode-desktop/tests.nix {
                inherit self;
                system = pkgs.stdenv.hostPlatform.system;
              }
            );

            "test-nix-opencode-mcp" = mkEvalOnlyCheck "test-nix-opencode-mcp" (
              { pkgs, ... }:
              import ./tests/nix/opencode-mcp.nix {
                inherit (pkgs) lib;
                src = ./.;
              }
            );

            "test-nix-package-helpers" = mkEvalOnlyCheck "test-nix-package-helpers" (
              _: import ./tests/nix/package-helpers.nix { src = ./.; }
            );

            "test-nix-source-hashes" = mkEvalOnlyCheck "test-nix-source-hashes" (
              { pkgs, ... }:
              import ./tests/nix/source-hashes.nix {
                inherit (pkgs) lib;
                src = ./.;
              }
            );

            "test-nix-direnv-batched-gcroots" = { pkgs, ... }: pkgs.nix-direnv.tests.batchedFlakeInputGcRoots;

            "test-nix-execline-darwin-symlinks" =
              { pkgs, ... }:
              if !pkgs.stdenv.hostPlatform.isDarwin then
                pkgs.runCommand "check-execline-darwin-symlinks-skipped" { } ''
                  touch "$out"
                ''
              else
                pkgs.runCommand "check-execline-darwin-symlinks"
                  {
                    nativeBuildInputs = [ pkgs.findutils ];
                  }
                  ''
                    # A separate builder is required because static evaluation
                    # cannot reproduce Cachix's unprivileged readlink boundary.
                    link_count=0
                    while IFS= read -r link; do
                      ${pkgs.coreutils}/bin/readlink "$link" >/dev/null
                      link_count=$((link_count + 1))
                    done < <(${pkgs.findutils}/bin/find ${pkgs.execline.bin}/bin -type l -print)

                    if (( link_count == 0 )); then
                      echo >&2 "execline installed no symlinks to validate"
                      exit 1
                    fi

                    touch "$out"
                  '';

            "test-nix-prefetch-git-darwin-heredoc" =
              { pkgs, ... }: import ./tests/nix/nix-prefetch-git-darwin-heredoc { inherit pkgs; };

            "test-nix-rio-overlay-platforms" = mkEvalOnlyCheck "test-nix-rio-overlay-platforms" (
              _: import ./tests/nix/rio-overlay-platforms { }
            );

            "test-zsh-gpg-tty" =
              { pkgs, ... }:
              import ./tests/nix/zsh-gpg-tty {
                inherit pkgs;
                src = ./.;
              };

            "test-nix-zsh-repo-plugins" =
              { pkgs, ... }:
              import ./tests/nix/zsh-repo-plugins.nix {
                inherit pkgs;
                inherit (pkgs) lib;
                src = ./.;
              };

            "package-codesnap-nvim" = { pkgs, ... }: pkgs.vimPlugins.codesnap-nvim;

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
            pkgs =
              (import ./lib/pinned-input-platform-compat).withLegacyPlatformAttrs
                baseOutputs.legacyPackages.${cfg.system};
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
