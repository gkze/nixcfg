{ inputs, outputs, ... }:
let
  normalizeName = s: builtins.replaceStrings [ "." "_" ] [ "-" "-" ] s;

  # Helper to strip version prefixes from flake refs
  stripVersionPrefix = s: builtins.replaceStrings [ "rust-v" "v" ] [ "" "" ] s;

  # Get version from flake lock, stripping common prefixes
  getFlakeVersion = name: stripVersionPrefix outputs.lib.flakeLock.${name}.original.ref;

  # Use pre-parsed sources from lib (avoids duplicate parsing)
  inherit (outputs.lib) sources;
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
      # Helper: Build a macOS .app from a .dmg with optional CLI symlink
      # ─────────────────────────────────────────────────────────────────────────
      mkDmgApp =
        {
          pname,
          info,
          appName ? pname,
          meta ? { },
        }:
        let
          arch = if system == "aarch64-darwin" then "aarch64" else "x86_64";
          capitalizedAppName =
            (prev.lib.toUpper (builtins.substring 0 1 appName)) + builtins.substring 1 (-1) appName;
        in
        prev.stdenvNoCC.mkDerivation {
          inherit pname;
          inherit (info) version;
          inherit meta;

          src = prev.fetchurl {
            name = "${capitalizedAppName}_${info.version}_${arch}.dmg";
            url = info.urls.${system};
            hash = info.hashes.${system};
          };

          nativeBuildInputs = [ prev.undmg ];

          sourceRoot = ".";

          installPhase = ''
            runHook preInstall

            mkdir -p "$out/Applications"
            mkdir -p "$out/bin"
            cp -a ${capitalizedAppName}.app "$out/Applications"
            /usr/bin/xattr -cr "$out/Applications/${capitalizedAppName}.app"
            ln -s "$out/Applications/${capitalizedAppName}.app/Contents/MacOS/${capitalizedAppName}" "$out/bin/${pname}"

            runHook postInstall
          '';
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
      # Python Package Overrides
      # ═══════════════════════════════════════════════════════════════════════════

      # mdformat: Update to 1.0.0 for markdown-it-py 4.x compatibility
      # nixos-unstable has markdown-it-py 4.0.0 but mdformat 0.7.22 requires <4.0.0
      # PR #483504 merged to master but not yet in unstable
      # TODO: Remove this override once nixos-unstable has mdformat 1.0.0
      mdformat = prev.mdformat.override {
        python3 = prev.python3.override {
          packageOverrides = _: pyPrev: {
            mdformat = pyPrev.mdformat.overridePythonAttrs (_: {
              version = getFlakeVersion "mdformat";
              src = inputs.mdformat;
            });
          };
        };
      };

      # ═══════════════════════════════════════════════════════════════════════════
      # Other Packages (with custom logic)
      # ═══════════════════════════════════════════════════════════════════════════

      # Patched BoringSSL source for rama-boring-sys (required by codex network-proxy)
      # rama-boring-sys builds BoringSSL from source, so we provide pre-patched source
      ramaBoringsslSource = prev.stdenvNoCC.mkDerivation {
        pname = "rama-boringssl-source";
        version = "79048f1-patched";
        src = inputs.rama-boringssl;
        phases = [
          "unpackPhase"
          "patchPhase"
          "installPhase"
        ];

        # Apply patches from rama-boring repo (in boring-sys/patches/)
        postPatch = ''
          patch -p1 < ${inputs.rama-boring}/boring-sys/patches/rama_tls.patch
          patch -p1 < ${inputs.rama-boring}/boring-sys/patches/rama_boring_pq.patch
        '';

        installPhase = ''
          cp -r . $out
        '';
      };

      codex =
        let
          version = getFlakeVersion "codex";
        in
        prev.codex.overrideAttrs (old: {
          inherit version;
          src = inputs.codex;
          sourceRoot = "source/codex-rs";
          cargoDeps = prev.rustPlatform.fetchCargoVendor {
            src = "${inputs.codex}/codex-rs";
            hash = outputs.lib.sourceHash "codex" "cargoHash";
          };

          nativeBuildInputs =
            (old.nativeBuildInputs or [ ])
            ++ (with prev; [
              cmake
              ninja
              go
              perl
            ]);

          # Provide patched BoringSSL source for rama-boring-sys
          BORING_BSSL_SOURCE_PATH = final.ramaBoringsslSource;
          BORING_BSSL_ASSUME_PATCHED = "1";
        });

      conductor = mkDmgApp {
        pname = "conductor";
        info = sources.conductor;
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
          mkDmgApp {
            pname = "sculptor";
            inherit info meta;
          }
        else
          let
            src = prev.fetchurl {
              name = "Sculptor_${info.version}.AppImage";
              url = info.urls.${system};
              hash = info.hashes.${system};
            };
          in
          prev.appimageTools.wrapType2 {
            pname = "sculptor";
            inherit (info) version;
            inherit meta src;

            extraInstallCommands =
              let
                appimageContents = prev.appimageTools.extractType2 {
                  inherit (info) version;
                  inherit src;
                  pname = "sculptor";
                };
              in
              ''
                # Install desktop file and icons if available
                if [ -d "${appimageContents}/usr/share" ]; then
                  cp -r "${appimageContents}/usr/share" "$out/"
                fi
              '';
          };

      # Factory Droid: AI coding agent CLI (prebuilt binary)
      droid =
        let
          info = sources.droid;
          inherit (info) version;
        in
        prev.stdenvNoCC.mkDerivation {
          pname = "droid";
          inherit version;

          src = prev.fetchurl {
            url = info.urls.${system};
            hash = info.hashes.${system};
          };

          dontUnpack = true;

          installPhase = ''
            runHook preInstall

            mkdir -p $out/bin
            cp $src $out/bin/droid
            chmod +x $out/bin/droid

            runHook postInstall
          '';

          meta = with prev.lib; {
            description = "Factory's AI coding agent";
            homepage = "https://factory.ai";
            license = licenses.unfree;
            platforms = [
              "aarch64-darwin"
              "x86_64-darwin"
              "aarch64-linux"
              "x86_64-linux"
            ];
            sourceProvenance = with sourceTypes; [ binaryNativeCode ];
            mainProgram = "droid";
          };
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

      linear-cli =
        let
          version = "1.8.1";
          # FOD: fetch Deno dependencies + run GraphQL codegen (both need network)
          # Uses platform-specific hash since deps include platform-specific binaries (lefthook)
          denoDeps = prev.stdenvNoCC.mkDerivation {
            pname = "linear-cli-deps";
            inherit version;
            src = inputs.linear-cli;
            nativeBuildInputs = [
              prev.deno
              prev.cacert
            ];
            outputHashAlgo = "sha256";
            outputHashMode = "recursive";
            outputHash = outputs.lib.sourceHashForPlatform "linear-cli" "denoDepsHash" system;
            buildPhase = ''
              export DENO_DIR=$TMPDIR/deno-cache
              export SSL_CERT_FILE=${prev.cacert}/etc/ssl/certs/ca-bundle.crt
              export HOME=$TMPDIR

              # Run codegen (generates src/__codegen__/graphql.ts via npm:@graphql-codegen/cli)
              deno task codegen

              # Cache all modules so deno compile works offline
              deno cache src/main.ts
            '';
            installPhase = ''
              mkdir -p $out
              cp -r $TMPDIR/deno-cache $out/deno-cache
              cp -r src/__codegen__ $out/codegen
            '';
          };
        in
        prev.stdenvNoCC.mkDerivation {
          pname = "linear-cli";
          inherit version;
          src = inputs.linear-cli;
          nativeBuildInputs = [
            prev.deno
            prev.installShellFiles
          ];
          buildPhase = ''
            export DENO_DIR=$(mktemp -d)
            cp -r ${denoDeps}/deno-cache/* $DENO_DIR/
            chmod -R u+w $DENO_DIR
            export HOME=$TMPDIR

            # Copy generated codegen files into source tree
            mkdir -p src/__codegen__
            cp -r ${denoDeps}/codegen/* src/__codegen__/

            deno compile -A --output linear src/main.ts
          '';
          installPhase = ''
            mkdir -p $out/bin
            cp linear $out/bin/

            installShellCompletion --cmd linear \
              --bash <($out/bin/linear completions bash) \
              --fish <($out/bin/linear completions fish) \
              --zsh <($out/bin/linear completions zsh)
          '';
          meta = with prev.lib; {
            description = "Linear issue tracker CLI";
            homepage = "https://github.com/schpet/linear-cli";
            license = licenses.isc;
            mainProgram = "linear";
          };
        };

      homebrew-zsh-completion =
        let
          source = outputs.lib.sourceHashEntry "homebrew-zsh-completion" "sha256";
        in
        prev.stdenvNoCC.mkDerivation {
          name = "brew-zsh-completion";
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

      # sentry-cli: Build from source with xcarchive test fixtures stripped.
      # Not a flake input because the repo contains .xcarchive bundles with
      # macOS code signatures that cause nix-store --optimise to fail
      # ("Operation not permitted" on _CodeSignature/CodeResources hardlinks).
      # Using fetchFromGitHub with postFetch lets us strip them before the
      # source enters the store — flake inputs are added unconditionally.
      sentry-cli =
        let
          filteredSrc = prev.fetchFromGitHub {
            owner = "getsentry";
            repo = "sentry-cli";
            tag = sources.sentry-cli.version;
            hash = outputs.lib.sourceHash "sentry-cli" "srcHash";
            postFetch = ''
              find $out -name '*.xcarchive' -type d -exec rm -rf {} +
            '';
          };
        in
        prev.sentry-cli.overrideAttrs (old: {
          inherit (sources.sentry-cli) version;
          src = filteredSrc;
          buildInputs = (old.buildInputs or [ ]) ++ [ prev.curl ];
          cargoDeps = prev.rustPlatform.fetchCargoVendor {
            src = filteredSrc;
            hash = outputs.lib.sourceHash "sentry-cli" "cargoHash";
          };
          # postFetch strips .xcarchive bundles (macOS code-signed), which
          # breaks this test that expects them present in the source tree.
          checkFlags = (old.checkFlags or [ ]) ++ [
            "--skip=commands::build::upload::tests::test_xcarchive_upload_includes_parsed_assets"
          ];
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

          nvim-treesitter-textobjects = vprev.nvim-treesitter-textobjects.overrideAttrs {
            src = inputs.treesitter-textobjects;
          };

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

      # ═══════════════════════════════════════════════════════════════════════════
      # Flake Input Packages (simple re-exports)
      # ═══════════════════════════════════════════════════════════════════════════

      flake-edit = inputs.flake-edit.packages.${system}.default;

      # Pin Swift to a nixpkgs rev where it builds (clang-21.1.8 broke it)
      # Tracking: https://github.com/NixOS/nixpkgs/issues/483584
      inherit (import inputs.nixpkgs-swift { inherit system; })
        swiftPackages
        swift
        ;

      # ═══════════════════════════════════════════════════════════════════════════
      # Update Script
      # ═══════════════════════════════════════════════════════════════════════════

      update-script =
        let
          inherit (inputs.pyproject-nix.lib) scripts;
          script = scripts.loadScript {
            name = "update";
            script = ./update.py;
          };
          unwrapped = prev.writeScriptBin script.name (
            scripts.renderWithPackages {
              inherit script;
              python = prev.python313;
            }
          );
        in
        prev.symlinkJoin {
          name = "update-script";
          paths = [ unwrapped ];
          nativeBuildInputs = [ prev.makeWrapper ];
          postBuild = ''
            wrapProgram $out/bin/update \
              --prefix PATH : ${prev.lib.makeBinPath [ final.flake-edit ]}
          '';
        };
    };
}
