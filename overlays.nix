{ inputs, outputs, ... }:
let
  normalizeName = s: builtins.replaceStrings [ "." "_" ] [ "-" "-" ] s;

  # Helper to strip version prefixes from flake refs
  stripVersionPrefix = s: builtins.replaceStrings [ "rust-v" "v" ] [ "" "" ] s;

  # Get version from flake lock, stripping common prefixes
  getFlakeVersion = name: stripVersionPrefix outputs.lib.flakeLock.${name}.original.ref;

  # Pre-parsed sources.json for all packages
  sources = builtins.fromJSON (builtins.readFile ./sources.json);
in
{
  default =
    final: prev:
    let
      inherit (prev.stdenv.hostPlatform) system;

      # ─────────────────────────────────────────────────────────────────────────
      # Helper: Override a package with version/src from sources.json
      # ─────────────────────────────────────────────────────────────────────────
      mkSourceOverride =
        name: pkg:
        let
          info = sources.${name};
        in
        pkg.overrideAttrs {
          inherit (info) version;
          src = prev.fetchurl {
            url = info.urls.${system} or info.urls.darwin;
            hash = info.hashes.${system};
          };
        };

      # ─────────────────────────────────────────────────────────────────────────
      # Helper: Build a Go CLI package with shell completions
      # ─────────────────────────────────────────────────────────────────────────
      mkGoCliPackage =
        {
          pname,
          input,
          subPackages,
          cmdName ? pname,
          version ? null,
          meta ? { },
          ...
        }@args:
        let
          flakeRef = outputs.lib.flakeLock.${pname};
          finalVersion =
            if version != null then version else stripVersionPrefix (flakeRef.original.ref or "");
        in
        prev.buildGoModule (
          {
            inherit pname subPackages;
            version = finalVersion;
            src = input;
            vendorHash = outputs.lib.sourceHash pname "vendorHash";
            doCheck = false;
            nativeBuildInputs = [ prev.installShellFiles ];
            postInstall = ''
              installShellCompletion --cmd ${cmdName} \
                --bash <($out/bin/${cmdName} completion bash) \
                --fish <($out/bin/${cmdName} completion fish) \
                --zsh <($out/bin/${cmdName} completion zsh)
            '';
            meta = {
              mainProgram = cmdName;
            }
            // meta;
          }
          // (builtins.removeAttrs args [
            "pname"
            "input"
            "subPackages"
            "cmdName"
            "version"
            "meta"
          ])
        );

      # ─────────────────────────────────────────────────────────────────────────
      # Helper: Build a Python package using uv2nix
      # ─────────────────────────────────────────────────────────────────────────
      mkUv2nixPackage =
        {
          name,
          src,
          pythonVersion ? prev.python313,
          mainProgram,
          packageName ? name,
          venvName ? name,
          extraBuildPhase ? "",
          extraOverlays ? [ ],
        }:
        let
          uv = prev.lib.getExe prev.uv;
          python = prev.lib.getExe pythonVersion;
          workspace = inputs.uv2nix.lib.workspace.loadWorkspace {
            workspaceRoot = prev.stdenv.mkDerivation {
              name = "${name}-locked";
              inherit src;
              buildPhase = ''
                ${extraBuildPhase}
                UV_PYTHON=${python} ${uv} -n lock
              '';
              installPhase = "cp -r . $out";
            };
          };
          pySet =
            (prev.callPackage inputs.pyproject-nix.build.packages {
              python = pythonVersion;
            }).overrideScope
              (
                prev.lib.composeManyExtensions (
                  [
                    inputs.pyproject-build-systems.overlays.default
                    (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
                  ]
                  ++ extraOverlays
                )
              );
        in
        (prev.callPackages inputs.pyproject-nix.build.util { }).mkApplication {
          venv = pySet.mkVirtualEnv venvName workspace.deps.all // {
            meta.mainProgram = mainProgram;
          };
          package = pySet.${packageName};
        };

      # ─────────────────────────────────────────────────────────────────────────
      # Helper: Patch opencode packages for bun version compatibility
      # ─────────────────────────────────────────────────────────────────────────
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
      # ═══════════════════════════════════════════════════════════════════════════
      # Go CLI Packages
      # ═══════════════════════════════════════════════════════════════════════════

      axiom-cli = mkGoCliPackage {
        pname = "axiom-cli";
        input = inputs.axiom-cli;
        subPackages = [ "cmd/axiom" ];
        cmdName = "axiom";
        meta = with prev.lib; {
          description = "The power of Axiom on the command line";
          homepage = "https://github.com/axiomhq/cli";
          license = licenses.mit;
        };
      };

      beads = mkGoCliPackage {
        pname = "beads";
        input = inputs.beads;
        subPackages = [ "cmd/bd" ];
        cmdName = "bd";
        version = "0.0.0"; # beads doesn't have version tags
        proxyVendor = true;
      };

      gogcli = mkGoCliPackage {
        pname = "gogcli";
        input = inputs.gogcli;
        subPackages = [ "cmd/gog" ];
        cmdName = "gog";
        meta = with prev.lib; {
          description = "Google Suite CLI: Gmail, GCal, GDrive, GContacts";
          homepage = "https://github.com/steipete/gogcli";
          license = licenses.mit;
        };
      };

      # ═══════════════════════════════════════════════════════════════════════════
      # Python (uv2nix) Packages
      # ═══════════════════════════════════════════════════════════════════════════

      beads-mcp = mkUv2nixPackage {
        name = "beads-mcp";
        src = "${inputs.beads}/integrations/beads-mcp";
        mainProgram = "beads-mcp";
      };

      nix-manipulator = mkUv2nixPackage {
        name = "nix-manipulator";
        src = inputs.nix-manipulator;
        mainProgram = "nima";
        extraBuildPhase = ''
          export SETUPTOOLS_SCM_PRETEND_VERSION=${getFlakeVersion "nix-manipulator"}
        '';
      };

      toad = mkUv2nixPackage {
        name = "toad";
        src = inputs.toad;
        pythonVersion = prev.python314;
        mainProgram = "toad";
        packageName = "batrachian-toad";
        venvName = "batrachian-toad";
        extraOverlays = [
          (f: p: {
            watchdog = p.watchdog.overrideAttrs (old: {
              buildInputs = (old.buildInputs or [ ]) ++ [ f.setuptools ];
              nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ f.setuptools ];
            });
          })
        ];
      };

      # ═══════════════════════════════════════════════════════════════════════════
      # sources.json Overrides (simple version + src updates)
      # ═══════════════════════════════════════════════════════════════════════════

      chatgpt = mkSourceOverride "chatgpt" prev.chatgpt;
      code-cursor = mkSourceOverride "code-cursor" prev.code-cursor;
      google-chrome = mkSourceOverride "google-chrome" prev.google-chrome;

      jetbrains = prev.jetbrains // {
        datagrip = mkSourceOverride "datagrip" prev.jetbrains.datagrip;
      };

      # ═══════════════════════════════════════════════════════════════════════════
      # Other Packages (with custom logic)
      # ═══════════════════════════════════════════════════════════════════════════

      codex =
        let
          version = getFlakeVersion "codex";
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
          info = sources.conductor;
          arch = if system == "aarch64-darwin" then "aarch64" else "x86_64";
        in
        prev.stdenvNoCC.mkDerivation {
          pname = "conductor";
          inherit (info) version;

          src = prev.fetchurl {
            name = "Conductor_${info.version}_${arch}.dmg";
            url = info.urls.${system};
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

      sculptor =
        let
          info = sources.sculptor;
          inherit (prev.stdenv) isDarwin;
          arch = if system == "aarch64-darwin" then "aarch64" else "x86_64";
          meta = with prev.lib; {
            description = "UI for running parallel coding agents in safe, isolated sandboxes";
            homepage = "https://imbue.com/sculptor/";
            license = licenses.unfree;
            platforms = [
              "aarch64-darwin"
              "x86_64-darwin"
              "x86_64-linux"
            ];
            sourceProvenance = with sourceTypes; [ binaryNativeCode ];
            mainProgram = "sculptor";
          };
        in
        if isDarwin then
          prev.stdenvNoCC.mkDerivation {
            pname = "sculptor";
            inherit (info) version;
            inherit meta;

            src = prev.fetchurl {
              name = "Sculptor_${info.version}_${arch}.dmg";
              url = info.urls.${system};
              hash = info.hashes.${system};
            };

            nativeBuildInputs = [ prev.undmg ];

            sourceRoot = ".";

            installPhase = ''
              runHook preInstall

              mkdir -p "$out/Applications"
              mkdir -p "$out/bin"
              cp -a Sculptor.app "$out/Applications"
              ln -s "$out/Applications/Sculptor.app/Contents/MacOS/Sculptor" "$out/bin/sculptor"

              runHook postInstall
            '';
          }
        else
          prev.appimageTools.wrapType2 {
            pname = "sculptor";
            inherit (info) version;
            inherit meta;

            src = prev.fetchurl {
              name = "Sculptor_${info.version}.AppImage";
              url = info.urls.${system};
              hash = info.hashes.${system};
            };

            extraInstallCommands =
              let
                appimageContents = prev.appimageTools.extractType2 {
                  inherit (info) version;
                  pname = "sculptor";
                  src = prev.fetchurl {
                    name = "Sculptor_${info.version}.AppImage";
                    url = info.urls.${system};
                    hash = info.hashes.${system};
                  };
                };
              in
              ''
                # Install desktop file and icons if available
                if [ -d "${appimageContents}/usr/share" ]; then
                  cp -r "${appimageContents}/usr/share" "$out/"
                fi
              '';
          };

      crush =
        let
          version = getFlakeVersion "crush";
        in
        prev.crush.overrideAttrs {
          inherit version;
          src = inputs.crush;
          vendorHash = outputs.lib.sourceHash "crush" "vendorHash";
        };

      gemini-cli =
        let
          version = getFlakeVersion "gemini-cli";
          npmDepsHash = outputs.lib.sourceHash "gemini-cli" "npmDepsHash";
          npmDeps = prev.fetchNpmDeps {
            src = inputs.gemini-cli;
            hash = npmDepsHash;
          };
        in
        prev.gemini-cli.overrideAttrs (oldAttrs: {
          inherit version npmDepsHash npmDeps;
          src = inputs.gemini-cli;
          disallowedReferences = [
            npmDeps
            prev.nodejs_22.python
          ];
          # Remove files that reference python to keep it out of the closure
          postInstall = (oldAttrs.postInstall or "") + ''
            rm -rf $out/share/gemini-cli/node_modules/keytar/build
          '';
        });

      # gitbutler removed - using Homebrew cask (Nix build blocked by git dep issues)
      # See bead nixcfg-uec for technical details

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

      opencode = inputs.opencode.packages.${system}.opencode.overrideAttrs opencodeBunPatch;

      opencode-desktop = inputs.opencode.packages.${system}.desktop.overrideAttrs (
        old:
        (opencodeBunPatch old)
        // {
          # Use dev config instead of prod (gets dev icon + "OpenCode Dev" name)
          tauriBuildFlags = [ "--no-sign" ];

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
          version = getFlakeVersion "sentry-cli";
          # Filter out iOS test fixtures with code-signed .xcarchive bundles
          # These cause nix-store --optimise to fail on macOS due to
          # code signature protections on _CodeSignature/CodeResources files
          # Note: lib.cleanSourceWith does NOT work on flake inputs (already in store)
          # Must use a derivation to physically copy and filter the source
          filteredSrc = prev.runCommand "sentry-cli-src-filtered" { } ''
            cp -r ${inputs.sentry-cli} $out
            chmod -R u+w $out
            find $out -name "*.xcarchive" -type d -exec rm -rf {} + 2>/dev/null || true
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
          info = sources.vscode-insiders;
          inherit (info) version;
          hash = info.hashes.${system};
          plat =
            {
              aarch64-darwin = "darwin-arm64";
              aarch64-linux = "linux-arm64";
              x86_64-darwin = "darwin";
              x86_64-linux = "linux-x64";
            }
            .${system};
          archive_fmt = if prev.stdenv.hostPlatform.isDarwin then "zip" else "tar.gz";
        in
        (prev.vscode.override { isInsiders = true; }).overrideAttrs {
          inherit version;
          src = prev.fetchurl {
            name = "VSCode-insiders-${version}-${plat}.${archive_fmt}";
            url = info.urls.${system};
            inherit hash;
          };
        };

      # Zed editor nightly from upstream flake
      zed-editor-nightly = inputs.zed.packages.${system}.default;
    };
}
