{
  description = "Universe";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    # OpenChamber's source-built addon requires exactly Sherpa 1.13.8 and its
    # validated ONNX runtime tuple. Keep this ABI pin local to that application.
    nixpkgs-openchamber-compat = {
      url = "github:NixOS/nixpkgs/5adc50831c259f87faeb09ddf930d43597197473";
      flake = false;
    };
    nix-homebrew = {
      # Fork pinned for zhaofengli/nix-homebrew#117 (a brew-src bump), merged
      # upstream 2025-10-31; brew-src is pinned below, so return to
      # github:zhaofengli/nix-homebrew at the next lock refresh.
      url = "github:Yeradon/nix-homebrew";
      # Homebrew 7.0.10 supports brew bundle --force-cleanup,
      # required by current nix-darwin. Keep the brew/tap pins tested as a tuple.
      inputs.brew-src.url = "github:Homebrew/brew/8f10429b56be28a25032b87463fffc95e556b5bc";
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
      url = "github:generalaction/emdash/v1.2.6";
      flake = false;
    };
    git-hooks = {
      url = "github:cachix/git-hooks.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    gitbutler = {
      url = "github:gitbutlerapp/gitbutler/release/0.22.3";
      flake = false;
    };
    home-manager = {
      url = "github:nix-community/home-manager";
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
      url = "github:gkze/opencode?ref=gkze/patches";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    hermes-agent = {
      url = "github:NousResearch/hermes-agent/v2026.9.21";
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
      # #1265 floated rust-overlay 8afee9fa→e90fafa7 (oxalica "manifest:
      # update" for nightly 2026-10-10). Plan-darwin-closures jumped from
      # warmup=4 / rustc-1.98.1 substitutable (#1264, 4ca29632) to
      # warmup=7812 (6515 rust_* plus file_cmds/dummyBuild). rust-warmup
      # then printed 529/634/1762 will-be-built on vendor-registry /
      # vendor-cargo-deps / goose-cli. Unpin after that nightly rustc is
      # in gkze.cachix.org.
      url = "github:oxalica/rust-overlay/4ca2963243427cb86bd6b4d9ca6010db0162a619";
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
      url = "github:max-sixty/worktrunk/v0.81.0";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    axiom-cli = {
      url = "github:axiomhq/cli/v0.20.0";
      flake = false;
    };
    anthropic-cli = {
      url = "github:anthropics/anthropic-cli/v2.0.0";
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
    catppuccin-starship-src = {
      # Match catppuccin/nix's Starship port without realizing its derivation at eval time.
      url = "github:catppuccin/starship/5906cc369dd8207e063c0e6e2d27bd0c0b567cb8";
      flake = false;
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
    # helix 25.07 points the gotmpl grammar at the deleted
    # dannylongeuay/tree-sitter-go-template repo, which breaks
    # `hx --grammar fetch`. Pin the replacement adopted in helix PR #14746;
    # remove once nixpkgs ships a helix release with the fixed grammar source.
    tree-sitter-go-template = {
      url = "github:ngalaiko/tree-sitter-go-template/aa71f63de226c5592dfbfc1f29949522d7c95fac";
      flake = false;
    };
    codex = {
      url = "github:openai/codex/rust-v0.163.0";
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
      url = "github:steipete/gogcli/v0.46.0";
      flake = false;
    };
    openai-cli = {
      url = "github:openai/openai-cli/v1.40.0";
      flake = false;
    };
    github-desktop = {
      type = "git";
      url = "https://github.com/desktop/desktop.git";
      ref = "refs/tags/release-3.6.6";
      submodules = true;
      flake = false;
    };
    goose = {
      type = "github";
      owner = "aaif-goose";
      repo = "goose";
      ref = "v1.55.0";
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
      url = "github:homebrew/homebrew-cask/37fbcfab1af565ebcf88a94611baac797681f2fa";
      flake = false;
    };
    homebrew-core = {
      # Retained tap snapshot; revalidate with brew-src changes.
      url = "github:homebrew/homebrew-core/36566489f7c7e828252cea8bcc303b4a7fc352de";
      flake = false;
    };
    hwatch = {
      url = "github:blacknon/hwatch/0.4.2";
      flake = false;
    };
    macfuse = {
      url = "github:macfuse/library";
      flake = false;
    };
    mux = {
      url = "github:coder/mux/v0.30.0";
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
      # Keep the source-built Darwin package on the same immutable desktop
      # release as the binary asset. `nixcfg update` advances version-like refs.
      url = "github:superset-sh/superset/desktop-v1.30.2";
      flake = false;
    };
    nix-manipulator = {
      url = "github:hoh/nix-manipulator/0.1.3";
      flake = false;
    };
    zon2nix = {
      # Keep Neutils generation on the exact revision already proven with its
      # Zig 0.15 dependency graph. Review upstream's first post-v0.6.0 release,
      # including the ZIP-fetch fixes, before deliberately advancing this pin.
      # Tracking: https://github.com/jcollie/zon2nix/compare/v0.6.0...main
      url = "github:jcollie/zon2nix/0ece5ed15107ecafbb121ad46aa85413dd40ff03";
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
      git-hooks,
      treefmt-nix,
      ...
    }@inputs:
    let
      systemPolicy = builtins.fromJSON (builtins.readFile ./lib/system-policy.json);
      systems =
        assert systemPolicy.schemaVersion == 1;
        builtins.attrNames systemPolicy.systems;

      configurationNamesIn =
        directory:
        if !builtins.pathExists directory then
          [ ]
        else
          map (inputs.nixpkgs.lib.removeSuffix ".nix") (
            builtins.attrNames (
              inputs.nixpkgs.lib.filterAttrs (
                name: type: type == "regular" && inputs.nixpkgs.lib.hasSuffix ".nix" name
              ) (builtins.readDir directory)
            )
          );
      requiredRootSources =
        map (name: {
          kind = "darwin";
          inherit name;
        }) (configurationNamesIn (./. + "/darwin"))
        ++ map (name: {
          kind = "nixos";
          inherit name;
        }) (configurationNamesIn (./. + "/nixos"))
        ++ map (name: {
          kind = "home";
          inherit name;
        }) (builtins.attrNames standaloneHomeEntries);

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

      overlayList = import ./lib/package-overlays.nix {
        inherit inputs;
        outputs = self;
      };
      baseOutputs = flakelight ./. (
        { config, lib, ... }:
        let
          configurationSystem = import ./lib/configuration-system.nix;
          rootClosureData = import ./lib/root-closures.nix {
            inherit lib systems;
            darwinConfigurations = self.darwinConfigurations or { };
            nixosConfigurations = self.nixosConfigurations or { };
            homeConfigurations = self.homeConfigurations or { };
            declaredSystems = {
              darwin = builtins.mapAttrs (_: configurationSystem) config.darwinConfigurations;
              nixos = builtins.mapAttrs (_: configurationSystem) config.nixosConfigurations;
              home = builtins.mapAttrs (_: cfg: cfg.system) standaloneHomeDefinitions;
            };
            requiredKinds = systemPolicy.requiredRootKinds;
            requiredRoots = requiredRootSources;
          };
          exports = import ./lib/exports.nix { src = ./.; };
          lintFiles = import ./lib/lint-files.nix;
          inherit (lintFiles.python)
            pyupgradePaths
            pythonPyupgradeExcludes
            ruffMutationExcludes
            ;
          mkDevShell = import ./lib/dev-shell.nix {
            src = ./.;
            gitHooks = git-hooks;
            inherit lib lintFiles mkNixcfgPackage;
          };
          # Package discovery already instantiates this environment in the
          # package set. Reuse it for checks, apps, formatting and the shell.
          mkNixcfgPackage = pkgs: pkgs.nixcfg;
          repoCheckSet = import ./lib/repo-checks.nix {
            src = ./.;
            inherit lib lintFiles;
          };
          inherit (repoCheckSet) mkCheckSource pytestFiles;
          mkRepoCheck =
            {
              name,
              runCommandAttrs ? { },
              repoWritable ? false,
              source,
              setup ? "",
              beforeCheck ? "",
              afterCheck ? "",
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
              ${resolve beforeCheck}
              ${resolve command}
              ${resolve afterCheck}
              touch $out
            '';
          # An eval-only check passes when its assertion evaluates to true; the
          # build output is only a stamp.
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
          # Every check name exists on every check system. A check whose
          # subject exists only on some platforms evaluates that subject only
          # there and is an empty stamp elsewhere. `when` receives
          # hostPlatform; `test` marks an eval-only check, `check` a builder.
          mkPlatformCheck =
            checkName:
            {
              when,
              test ? null,
              check ? null,
            }:
            { pkgs, ... }@context:
            if !(when pkgs.stdenv.hostPlatform) then
              pkgs.runCommand "check-${checkName}-skipped" { } ''
                touch "$out"
              ''
            else if test != null then
              mkEvalOnlyCheck checkName test context
            else
              check context;
          isDarwin = platform: platform.isDarwin;
          isLinux = platform: platform.isLinux;
          # The Darwin hosts and the standalone Home Manager configuration are
          # all aarch64-darwin; their evaluation belongs to that check system.
          isConfigurationSystem = platform: platform.system == "aarch64-darwin";
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
                      nixcfgPkg = mkNixcfgPackage context.pkgs;
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
          repoCheckSpecs = repoCheckSet.checks // {
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
                  ${nixcfgVenv}/bin/python -c \
                    'import json, jsonschema, sys; json.load(open(sys.argv[1], encoding="utf-8"))' \
                    "$site_packages/lib/schema_codegen/codegen.schema.json"
                '';
            };

            "test-python-pytest" = {
              repoWritable = true;
              nixcfg = true;
              source = mkCheckSource pytestFiles;
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
                  export NIXCFG_REQUIRE_NIX_EVAL=true
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
                  ln -s ${pkgs.typescript_5}/lib/node_modules/typescript node_modules/typescript
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
          disabledModules = [
            "homeConfigurations.nix"
            # The local adapter derives check names from constructor metadata.
            "${flakelight-darwin}/flakelight-darwin/darwinConfigurations.nix"
          ];

          outputs.lib.rootClosureManifest = rootClosureData.manifest;

          outputs.checks = lib.genAttrs rootClosureData.rootSystems (
            system:
            let
              pkgs = baseOutputs.legacyPackages.${system};
              roots = rootClosureData.forSystem system;
            in
            {
              root-closures = pkgs.linkFarm "nixcfg-root-closures" roots;
            }
            // builtins.listToAttrs (
              map (root: {
                name = "root-closure-${root.name}";
                value = root.path;
              }) roots
            )
          );

          # The single owner of Nixpkgs `config`. mkSystem passes the resulting
          # `pkgsFor` instance to each configuration as `nixpkgs.pkgs`.
          nixpkgs.config = nixpkgsConfig;

          # flakelight's propagation module would forward `nixpkgs.config` and
          # the overlays into each configuration again. Next to an external
          # `nixpkgs.pkgs` that trips nix-darwin's assertion and re-applies
          # every overlay through `appendOverlays`. Its `flake` module
          # argument has no readers.
          propagationModule = lib.mkForce { };

          apps.nixcfg =
            pkgs:
            let
              nixcfgPkg = mkNixcfgPackage pkgs;
            in
            {
              program = "${nixcfgPkg}/bin/nixcfg";
              meta.description = "Unified CLI for nixcfg project tasks.";
            };

          imports = [
            flakelight-darwin.flakelightModules.default
            ./lib/flakelight-darwin-configurations.nix
          ];

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
                            # Skill files start with YAML frontmatter; without
                            # this plugin mdformat rewrites the `---` fences as
                            # a thematic break and destroys the metadata.
                            mdformat-frontmatter
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

          checks =
            builtins.mapAttrs mkEvalOnlyCheck {
              "test-nix-default-api" = _: import ./tests/nix/default-api/default-api.nix { src = ./.; };

              "test-nix-crate-cache-boundaries" =
                { pkgs, ... }:
                import ./tests/nix/crate-cache-boundaries.nix {
                  inherit (pkgs) lib;
                  system = pkgs.stdenv.hostPlatform.system;
                };

              "test-nix-zed-build-rust-crate-functor" =
                { pkgs, ... }: import ./tests/nix/zed-build-rust-crate-functor.nix { inherit (pkgs) lib; };

              "test-nix-darwin-mksystem" =
                _:
                import ./tests/nix/darwin-mksystem.nix {
                  inherit (inputs.nix-darwin.lib) darwinSystem;
                  inherit (self.lib) mkSystem;
                };

              "test-nix-dock-defaults" =
                { pkgs, ... }:
                import ./tests/nix/dock-defaults.nix {
                  inherit (pkgs) lib;
                  src = ./.;
                };

              "test-nix-language-path-modules" =
                { pkgs, ... }:
                import ./tests/nix/language-path-modules.nix {
                  inherit (pkgs) lib;
                  src = ./.;
                };

              "test-nix-common-maintenance" =
                { pkgs, ... }:
                import ./tests/nix/common-maintenance.nix {
                  inherit (pkgs) lib;
                  src = ./.;
                };

              "test-nix-opencode-desktop" =
                { pkgs, ... }:
                import ./packages/opencode-desktop/tests.nix {
                  inherit self;
                  system = pkgs.stdenv.hostPlatform.system;
                };

              "test-nix-opencode-mcp" =
                { pkgs, ... }:
                import ./tests/nix/opencode-mcp.nix {
                  inherit (pkgs) lib;
                  src = ./.;
                };

              "test-nix-opencode-profile" =
                { pkgs, ... }:
                import ./tests/nix/opencode-profile.nix {
                  inherit (pkgs) lib;
                  src = ./.;
                };

              "test-nix-jsonc" =
                { pkgs, ... }:
                import ./tests/nix/jsonc.nix {
                  inherit (pkgs) lib;
                  src = ./.;
                };

              "test-nix-repo-checks" =
                { pkgs, ... }:
                import ./tests/nix/repo-checks.nix {
                  inherit (pkgs) lib;
                  src = ./.;
                };

              "test-nix-package-helpers" =
                { pkgs, ... }:
                assert import ./tests/nix/t3code-workspace-source.nix { inherit (pkgs) lib; };
                assert import ./tests/nix/opencode-desktop-source.nix { inherit (pkgs) lib; };
                assert import ./tests/nix/package-probes.nix {
                  inherit pkgs;
                  src = ./.;
                };
                import ./tests/nix/package-helpers.nix { src = ./.; };

              "test-nix-root-closures" =
                _:
                import ./tests/nix/root-closures.nix {
                  actualManifest = rootClosureData.manifest;
                  flakelight = inputs.flakelight.lib;
                  inherit lib;
                  src = ./.;
                };

              "test-nix-source-hashes" =
                { pkgs, ... }:
                import ./tests/nix/source-hashes.nix {
                  inherit (pkgs) lib;
                  src = ./.;
                };

              "test-nix-version-prefixes" = _: import ./tests/nix/version-prefixes.nix { inherit (self) lib; };

              "test-nix-rio-overlay-platforms" = _: import ./tests/nix/rio-overlay-platforms { };

              "test-nix-binary-darwin-apps-platforms" = _: import ./tests/nix/binary-darwin-apps-platforms { };

              "test-nix-build-rust-crate-unique-drvs" =
                { pkgs, ... }: import ./tests/nix/build-rust-crate-unique-drvs.nix { inherit pkgs; };

              "test-nix-sign-and-verify-bundle" =
                { pkgs, ... }: import ./tests/nix/sign-and-verify-bundle.nix { inherit pkgs; };
            }
            // builtins.mapAttrs mkPlatformCheck {
              # Runtime launch check for the Linux desktop app. The bundle is
              # repacked from OpenAI's Debian artifact, so only actually
              # starting it proves the package works: this runs the app under
              # a private Xvfb display and fails unless it presents a window
              # and holds it.
              # The check adds Chromium --no-sandbox flags because nix-daemon's
              # sandbox cannot nest Chromium's (38553752589 validate-arm).
              "chatgpt-linux-launch" = {
                when = isLinux;
                check = { pkgs, ... }: pkgs.callPackage ./tests/nix/chatgpt-linux-launch.nix { };
              };

              "test-nix-buzz-export-readiness" = {
                when = isConfigurationSystem;
                test =
                  { pkgs, ... }:
                  import ./tests/nix/buzz-export-readiness.nix {
                    inherit pkgs;
                    src = ./.;
                  };
              };

              "test-nix-gpg-session" = {
                when = isConfigurationSystem;
                test = _: import ./tests/nix/gpg-session { inherit self; };
              };

              "test-nix-nvim-keymaps" = {
                when = isConfigurationSystem;
                test =
                  _:
                  import ./tests/nix/nvim-keymaps.nix {
                    config = self.homeConfigurations.george.config;
                    src = ./.;
                  };
              };

              "test-nix-codex-bundled-plugin-repair" = {
                when = isDarwin;
                check =
                  { pkgs, ... }:
                  import ./tests/nix/codex-bundled-plugin-repair.nix {
                    inherit pkgs;
                    inherit (pkgs) lib;
                    src = ./.;
                  };
              };

              "test-nix-execline-darwin-symlinks" = {
                when = isDarwin;
                check =
                  { pkgs, ... }:
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
              };
            }
            // {
              # Fails when any formatter would change a file. flakelight runs
              # string checks in a writable copy of the flake source.
              "format-repo" =
                { lib, outputs', ... }:
                ''
                  ${lib.getExe outputs'.formatter} --ci .
                '';

              "test-nix-crate2nix-source-slice" =
                { pkgs, ... }:
                import ./tests/nix/crate2nix-source-slice.nix {
                  inherit pkgs;
                  src = ./.;
                };

              "test-nix-direnv-batched-gcroots" = { pkgs, ... }: pkgs.nix-direnv.tests.batchedFlakeInputGcRoots;

              "test-nix-prefetch-git-darwin-heredoc" =
                { pkgs, ... }: import ./tests/nix/nix-prefetch-git-darwin-heredoc { inherit pkgs; };

              "test-fetch-cargo-vendor-network" =
                { pkgs, ... }: import ./tests/nix/fetch-cargo-vendor.nix { inherit pkgs; };

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
          }
        );
      standaloneHomeEntries = inputs.nixpkgs.lib.filterAttrs (
        name: type: type == "directory" && builtins.pathExists (./home + "/${name}/default.nix")
      ) (builtins.readDir ./home);
      standaloneHomeDefinitions = builtins.mapAttrs (
        name: _: import (./home + "/${name}") { outputs = self; }
      ) standaloneHomeEntries;
      standaloneHomeConfigurations = builtins.mapAttrs mkStandaloneHomeConfiguration standaloneHomeDefinitions;
    in
    (builtins.removeAttrs baseOutputs [
      "checks"
      "legacyPackages"
    ])
    // {
      homeConfigurations = standaloneHomeConfigurations;

      checks = builtins.mapAttrs (
        _system: systemChecks:
        # format-repo replaces flakelight's in-place `formatting` check.
        builtins.removeAttrs systemChecks [ "formatting" ]
      ) baseOutputs.checks;
      pkgs = baseOutputs.legacyPackages;
    };
}
