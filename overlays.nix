{ inputs, outputs, ... }:
let
  normalizeName = s: builtins.replaceStrings [ "." "_" ] [ "-" "-" ] s;
  dedupCargoLockScript = ./misc/dedup_cargo_lock.py;
in
{
  default =
    final: prev:
    let
      opencodeBunPatch = old: {
        nativeBuildInputs =
          (old.nativeBuildInputs or [ ])
          ++ (with prev; [
            findutils
            jq
            moreutils
            bun
          ]);
        postPatch = ''
          # Update all package.json files with packageManager field to use current bun version
          bunVersion=$(bun -v | tr -d '\n')
          find . -name 'package.json' -exec sh -c '
            if jq -e ".packageManager" "$1" >/dev/null 2>&1; then
              jq --arg bunVersion "'"$bunVersion"'" ".packageManager = \"bun@\(\$bunVersion)\"" "$1" | sponge "$1"
            fi
          ' _ {} \;
        '';
      };
    in
    {
      axiom-cli =
        let
          flakeRef = outputs.lib.flakeLock.axiom-cli;
        in
        prev.buildGoModule {
          pname = "axiom-cli";
          version = flakeRef.original.ref;
          src = inputs.axiom-cli;
          subPackages = [ "cmd/axiom" ];
          vendorHash = outputs.lib.sourceHash "axiom-cli" "vendorHash";
          doCheck = false;

          nativeBuildInputs = [ prev.installShellFiles ];

          postInstall = ''
            installShellCompletion --cmd axiom \
              --bash <($out/bin/axiom completion bash) \
              --fish <($out/bin/axiom completion fish) \
              --zsh <($out/bin/axiom completion zsh)
          '';

          meta = with prev.lib; {
            description = "The power of Axiom on the command line";
            homepage = "https://github.com/axiomhq/cli";
            license = licenses.mit;
            mainProgram = "axiom";
          };
        };

      beads = prev.buildGoModule {
        name = "beads";
        src = inputs.beads;
        subPackages = [ "cmd/bd" ];
        vendorHash = outputs.lib.sourceHash "beads" "vendorHash";
        proxyVendor = true;
        doCheck = false;

        nativeBuildInputs = [ prev.installShellFiles ];

        postInstall = ''
          installShellCompletion --cmd bd \
            --bash <($out/bin/bd completion bash) \
            --fish <($out/bin/bd completion fish) \
            --zsh <($out/bin/bd completion zsh)
        '';
      };

      beads-mcp =
        with inputs;
        let
          uv = prev.lib.getExe prev.uv;
          python = prev.lib.getExe prev.python313;
          workspace = uv2nix.lib.workspace.loadWorkspace {
            workspaceRoot = prev.stdenv.mkDerivation {
              name = "beads-mcp-locked";
              src = "${beads}/integrations/beads-mcp";
              buildPhase = "UV_PYTHON=${python} ${uv} -n lock";
              installPhase = "cp -r . $out";
            };
          };
          pySet =
            (prev.callPackage pyproject-nix.build.packages {
              python = prev.python313;
            }).overrideScope
              (
                prev.lib.composeManyExtensions [
                  pyproject-build-systems.overlays.default
                  (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
                ]
              );
        in
        (prev.callPackages pyproject-nix.build.util { }).mkApplication {
          venv = pySet.mkVirtualEnv "beads-mcp" workspace.deps.all // {
            meta.mainProgram = "beads-mcp";
          };
          package = pySet.beads-mcp;
        };

      chatgpt =
        let
          info = (builtins.fromJSON (builtins.readFile ./sources.json)).chatgpt;
          inherit (prev.stdenv.hostPlatform) system;
        in
        prev.chatgpt.overrideAttrs {
          inherit (info) version;
          src = prev.fetchurl {
            url = info.urls.darwin;
            hash = info.hashes.${system};
          };
        };

      code-cursor =
        let
          info = (builtins.fromJSON (builtins.readFile ./sources.json)).code-cursor;
          inherit (prev.stdenv.hostPlatform) system;
          urls = {
            aarch64-darwin = "https://downloads.cursor.com/production/${info.commit}/darwin/arm64/Cursor-darwin-arm64.dmg";
            x86_64-darwin = "https://downloads.cursor.com/production/${info.commit}/darwin/x64/Cursor-darwin-x64.dmg";
            aarch64-linux = "https://downloads.cursor.com/production/${info.commit}/linux/arm64/Cursor-${info.version}-aarch64.AppImage";
            x86_64-linux = "https://downloads.cursor.com/production/${info.commit}/linux/x64/Cursor-${info.version}-x86_64.AppImage";
          };
        in
        prev.code-cursor.overrideAttrs {
          inherit (info) version;
          src = prev.fetchurl {
            url = urls.${system};
            hash = info.hashes.${system};
          };
        };

      codex =
        let
          version = builtins.replaceStrings [ "rust-v" ] [ "" ] outputs.lib.flakeLock.codex.original.ref;
        in
        prev.codex.overrideAttrs {
          inherit version;
          src = inputs.codex;
          sourceRoot = "source/codex-rs";
          cargoDeps = prev.rustPlatform.fetchCargoVendor {
            src = "${inputs.codex}/codex-rs";
            hash = outputs.lib.sourceHash "codex" "cargoHash";
          };
        };

      conductor =
        let
          info = (builtins.fromJSON (builtins.readFile ./sources.json)).conductor;
          inherit (prev.stdenv.hostPlatform) system;
          arch = if system == "aarch64-darwin" then "aarch64" else "x86_64";
          urls = {
            aarch64-darwin = "https://cdn.crabnebula.app/download/melty/conductor/latest/platform/dmg-aarch64";
            x86_64-darwin = "https://cdn.crabnebula.app/download/melty/conductor/latest/platform/dmg-x86_64";
          };
        in
        prev.stdenvNoCC.mkDerivation {
          pname = "conductor";
          inherit (info) version;

          src = prev.fetchurl {
            name = "Conductor_${info.version}_${arch}.dmg";
            url = urls.${system};
            hash = info.hashes.${system};
          };

          nativeBuildInputs = [ prev.undmg ];

          sourceRoot = ".";

          installPhase = ''
            runHook preInstall

            mkdir -p "$out/Applications"
            mkdir -p "$out/bin"
            cp -a Conductor.app "$out/Applications"
            ln -s "$out/Applications/Conductor.app/Contents/MacOS/Conductor" "$out/bin/conductor"

            runHook postInstall
          '';

          meta = with prev.lib; {
            description = "Run a team of coding agents on your Mac";
            homepage = "https://www.conductor.build/";
            license = licenses.unfree;
            platforms = platforms.darwin;
            sourceProvenance = with sourceTypes; [ binaryNativeCode ];
            mainProgram = "conductor";
          };
        };

      crush =
        let
          version = builtins.replaceStrings [ "v" ] [ "" ] outputs.lib.flakeLock.crush.original.ref;
        in
        prev.crush.overrideAttrs {
          inherit version;
          src = inputs.crush;
          vendorHash = outputs.lib.sourceHash "crush" "vendorHash";
        };

      gemini-cli =
        let
          version = builtins.replaceStrings [ "v" ] [ "" ] outputs.lib.flakeLock.gemini-cli.original.ref;
          npmDepsHash = outputs.lib.sourceHash "gemini-cli" "npmDepsHash";
        in
        prev.gemini-cli.overrideAttrs rec {
          inherit version npmDepsHash;
          src = inputs.gemini-cli;
          npmDeps = prev.fetchNpmDeps {
            inherit src;
            hash = npmDepsHash;
          };
        };

      gitbutler =
        let
          version = "0.18.3";
          pnpmDepsHash = "sha256-R1EYyMy0oVX9G6GYrjIsWx7J9vfkdM4fLlydteVsi7E=";

          # Patch source to remove duplicate git sources from Cargo.lock
          # GitButler's Cargo.lock has some crates from both crates.io AND git,
          # which causes "File exists" errors during cargo vendor
          patchedSrc = prev.stdenvNoCC.mkDerivation {
            name = "gitbutler-src-deduped";
            src = inputs.gitbutler;
            nativeBuildInputs = [ prev.python3 ];
            patchPhase = ''
              python3 ${dedupCargoLockScript} Cargo.lock
            '';
            installPhase = ''
              cp -r . $out
            '';
          };
        in
        prev.gitbutler.overrideAttrs (old: {
          inherit version;
          src = patchedSrc;
          # Our dedup script replaces the nixpkgs Cargo.lock patch
          patches = [ ];
          cargoDeps = prev.rustPlatform.fetchCargoVendor {
            src = patchedSrc;
            hash = "sha256-2Qh26vhtKaJAmedjgMNf0rMQGslMk9qCO5Qz+NOK4Ys=";
          };
          pnpmDeps = prev.fetchPnpmDeps {
            inherit (old) pname;
            inherit version;
            src = patchedSrc;
            fetcherVersion = 2;
            hash = pnpmDepsHash;
          };
          # Override postPatch for 0.18.3 (code changed from 0.15.10)
          postPatch = ''
            tauriConf="crates/gitbutler-tauri/tauri.conf.release.json"

            # Set version, disable updater artifacts, remove externalBin
            jq '
              .version = "${version}" |
              .bundle.createUpdaterArtifacts = false |
              del(.bundle.externalBin)
            ' "$tauriConf" | sponge "$tauriConf"

            tomlq -ti 'del(.lints) | del(.workspace.lints)' \
              "$cargoDepsCopy"/gix*/Cargo.toml

            substituteInPlace apps/desktop/src/lib/backend/tauri.ts \
              --replace-fail 'checkUpdate = tauriCheck;' \
              'checkUpdate = () => null;'
          '';
          # Disable tests - snapshot tests fail due to date differences (25y ago vs 26y ago)
          doCheck = false;
        });

      google-chrome =
        let
          info = (builtins.fromJSON (builtins.readFile ./sources.json)).google-chrome;
          urls = {
            aarch64-darwin = "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg";
            x86_64-darwin = "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg";
            x86_64-linux = "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb";
          };
          inherit (prev.stdenv.hostPlatform) system;
        in
        prev.google-chrome.overrideAttrs {
          inherit (info) version;
          src = prev.fetchurl {
            url = urls.${system};
            hash = info.hashes.${system};
          };
        };

      homebrew-zsh-completion =
        let
          source = outputs.lib.sourceHashEntry "homebrew-zsh-completion" "sha256";
        in
        prev.stdenvNoCC.mkDerivation {
          name = "brew-zsh-compmletion";
          src = builtins.fetchurl {
            inherit (source) url;
            sha256 = source.hash;
          };
          dontUnpack = true;
          installPhase = ''
            mkdir $out/
            cp -r $src $out/_brew
            chmod +x $out/_brew
          '';
        };

      jetbrains = prev.jetbrains // {
        datagrip =
          let
            info = (builtins.fromJSON (builtins.readFile ./sources.json)).datagrip;
            inherit (prev.stdenv.hostPlatform) system;
            urls = {
              aarch64-darwin = "https://download.jetbrains.com/datagrip/datagrip-${info.version}-aarch64.dmg";
              x86_64-darwin = "https://download.jetbrains.com/datagrip/datagrip-${info.version}.dmg";
              aarch64-linux = "https://download.jetbrains.com/datagrip/datagrip-${info.version}-aarch64.tar.gz";
              x86_64-linux = "https://download.jetbrains.com/datagrip/datagrip-${info.version}.tar.gz";
            };
          in
          prev.jetbrains.datagrip.overrideAttrs {
            inherit (info) version;
            src = prev.fetchurl {
              url = urls.${system};
              hash = info.hashes.${system};
            };
          };
      };

      mountpoint-s3 = prev.mountpoint-s3.overrideAttrs (old: {
        buildInputs =
          prev.lib.optionals prev.stdenv.hostPlatform.isDarwin [ prev.macfuse-stubs ]
          ++ prev.lib.optionals prev.stdenv.hostPlatform.isLinux [ prev.fuse3 ];
        # Disable tests on Darwin - they require macFUSE to be installed at
        # /usr/local/lib/libfuse.2.dylib which isn't available in sandbox
        doCheck = !prev.stdenv.hostPlatform.isDarwin;
        meta = old.meta // {
          platforms = prev.lib.platforms.unix;
        };
      });

      nix-manipulator =
        with inputs;
        let
          version = outputs.lib.flakeLock.nix-manipulator.original.ref;
          uv = prev.lib.getExe prev.uv;
          python = prev.lib.getExe prev.python313;
          workspace = uv2nix.lib.workspace.loadWorkspace {
            workspaceRoot = prev.stdenv.mkDerivation {
              name = "nix-manipulator-locked";
              src = nix-manipulator;
              buildPhase = ''
                export SETUPTOOLS_SCM_PRETEND_VERSION=${version}
                UV_PYTHON=${python} ${uv} -n lock
              '';
              installPhase = "cp -r . $out";
            };
          };
          pySet =
            (prev.callPackage pyproject-nix.build.packages {
              python = prev.python313;
            }).overrideScope
              (
                prev.lib.composeManyExtensions [
                  pyproject-build-systems.overlays.default
                  (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
                ]
              );
        in
        (prev.callPackages pyproject-nix.build.util { }).mkApplication {
          venv = pySet.mkVirtualEnv "nix-manipulator" workspace.deps.all // {
            meta.mainProgram = "nima";
          };
          package = pySet.nix-manipulator;
        };

      opencode = inputs.opencode.packages.${prev.system}.opencode.overrideAttrs opencodeBunPatch;

      opencode-desktop = inputs.opencode.packages.${prev.system}.desktop.overrideAttrs (
        old:
        (opencodeBunPatch old)
        // {
          # Override preBuild to use our patched opencode instead of upstream's
          preBuild = ''
            cp -a ${old.node_modules}/{node_modules,packages} .
            chmod -R u+w node_modules packages
            patchShebangs node_modules
            patchShebangs packages/desktop/node_modules

            mkdir -p packages/desktop/src-tauri/sidecars
            cp ${final.opencode}/bin/opencode packages/desktop/src-tauri/sidecars/opencode-cli-${prev.stdenv.hostPlatform.rust.rustcTarget}
          '';
        }
      );

      sentry-cli =
        let
          version = outputs.lib.flakeLock.sentry-cli.original.ref;
          # Filter out iOS test fixtures with code-signed .xcarchive bundles
          # These cause nix-store --optimise to fail on macOS due to
          # code signature protections on _CodeSignature/CodeResources files
          filteredSrc = prev.runCommand "sentry-cli-src-filtered" { } ''
            cp -r ${inputs.sentry-cli} $out
            chmod -R u+w $out
            rm -rf $out/tests/integration/_fixtures/build/*.xcarchive
          '';
        in
        prev.sentry-cli.overrideAttrs (old: {
          inherit version;
          src = filteredSrc;
          cargoDeps = prev.rustPlatform.fetchCargoVendor {
            src = filteredSrc;
            hash = outputs.lib.sourceHash "sentry-cli" "cargoHash";
          };
          buildInputs = old.buildInputs or [ ] ++ [ prev.curl ];
          # Disable tests that depend on xcarchive fixtures we removed
          doCheck = false;
        });

      sublime-kdl =
        let
          flakeRef = outputs.lib.flakeLock.sublime-kdl;
        in
        prev.stdenvNoCC.mkDerivation {
          pname = normalizeName flakeRef.original.repo;
          version = flakeRef.original.ref;
          src = inputs.sublime-kdl;
          installPhase = "cp -r $src $out";
        };

      toad =
        with inputs;
        let
          uv = prev.lib.getExe prev.uv;
          python = prev.lib.getExe prev.python314;
          workspace = uv2nix.lib.workspace.loadWorkspace {
            workspaceRoot = prev.stdenv.mkDerivation {
              name = "toad-relocked";
              src = toad;
              buildPhase = ''
                UV_PYTHON=${python} ${uv} -n lock
              '';
              installPhase = "cp -r . $out";
            };
          };

          pySet =
            (prev.callPackage pyproject-nix.build.packages {
              python = prev.python314;
            }).overrideScope
              (
                prev.lib.composeManyExtensions [
                  pyproject-build-systems.overlays.default
                  (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
                  (f: p: {
                    watchdog = p.watchdog.overrideAttrs (old: {
                      buildInputs = (old.buildInputs or [ ]) ++ [ f.setuptools ];
                      nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ f.setuptools ];
                    });
                  })
                ]
              );
        in
        (prev.callPackages pyproject-nix.build.util { }).mkApplication {
          venv = pySet.mkVirtualEnv "batrachian-toad" workspace.deps.all // {
            meta.mainProgram = "toad";
          };
          package = pySet.batrachian-toad;
        };

      # Extend vimPlugins with fixes and custom plugins
      vimPlugins = prev.vimPlugins.extend (
        _: vprev: {
          # codesnap-nvim: Screenshot plugin for Neovim
          # Requires patches to work correctly with Nix-built native library:
          codesnap-nvim = vprev.codesnap-nvim.overrideAttrs (old: {
            postPatch =
              let
                # Bug 1: cpath pollution
                # The original code adds the full dylib FILE PATH to package.cpath, but cpath
                # expects directory PATTERNS like "/path/?.so". This pollutes the cpath and
                # breaks other C modules (e.g., blink.cmp). Fix: use proper cpath pattern.
                moduleLuaOld = ''package.cpath = path_utils.join(";", package.cpath, generator_path)'';
                moduleLuaNew = ''
                  local lib_dir = vim.fn.fnamemodify(generator_path, ":h")
                  package.cpath = package.cpath .. ";" .. lib_dir .. sep .. "lib?." .. module.get_lib_extension()'';

                # Bug 2: fetch.ensure_lib() tries to write to Nix store
                # Nixpkgs pre-builds and hardcodes the lib path, but the original function
                # still attempts mkdir/download/write operations in the read-only store.
                # Fix: short-circuit to return the pre-built library path directly.
                fetchLuaOld = "function fetch.ensure_lib()";
                fetchLuaNew = ''
                  function fetch.ensure_lib()
                    return "${vprev.codesnap-nvim.passthru.codesnap-lib}/lib/libgenerator.dylib"
                  end
                  function fetch._original_ensure_lib()'';
              in
              (old.postPatch or "")
              + ''
                substituteInPlace lua/codesnap/module.lua \
                  --replace-fail '${moduleLuaOld}' '${moduleLuaNew}'

                substituteInPlace lua/codesnap/fetch.lua \
                  --replace-fail '${fetchLuaOld}' '${fetchLuaNew}'
              '';
          });

          vim-bundle-mako = prev.vimUtils.buildVimPlugin {
            pname = normalizeName outputs.lib.flakeLock.vim-bundle-mako.original.repo;
            version = inputs.vim-bundle-mako.rev;
            src = inputs.vim-bundle-mako;
          };

          # Override opencode-nvim to use our patched opencode (fixes bun version mismatch)
          opencode-nvim = vprev.opencode-nvim.overrideAttrs (old: {
            dependencies = map (dep: if dep.pname or "" == "opencode" then final.opencode else dep) (
              old.dependencies or [ ]
            );
            propagatedBuildInputs = map (dep: if dep.pname or "" == "opencode" then final.opencode else dep) (
              old.propagatedBuildInputs or [ ]
            );
          });
        }
      );

      vscode-insiders =
        let
          info = (builtins.fromJSON (builtins.readFile ./sources.json)).vscode-insiders;
          inherit (info) version;
          hash = info.hashes.${prev.stdenv.hostPlatform.system};
          plat =
            {
              aarch64-darwin = "darwin-arm64";
              aarch64-linux = "linux-arm64";
              x86_64-darwin = "darwin";
              x86_64-linux = "linux-x64";
            }
            .${prev.stdenv.hostPlatform.system};
          archive_fmt = if prev.stdenv.hostPlatform.isDarwin then "zip" else "tar.gz";
        in
        (prev.vscode.override { isInsiders = true; }).overrideAttrs {
          inherit version;
          src = prev.fetchurl {
            name = "VSCode-insiders-${version}-${plat}.${archive_fmt}";
            url = "https://update.code.visualstudio.com/${version}/${plat}/insider";
            inherit hash;
          };
        };

      # Use upstream Zed flake for nightly builds (includes Cachix binary cache)
      zed-editor-nightly = inputs.zed.packages.${prev.stdenv.hostPlatform.system}.default;
    };
}
