{ inputs, outputs, ... }:
let
  normalizeName = s: builtins.replaceStrings [ "." "_" ] [ "-" "-" ] s;
in
{
  default =
    _: prev:
    let
      naersk' = prev.callPackage inputs.naersk { };
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
          vendorHash = "sha256-ULiXQxJl8hqWUY04cyjXWUefPoC5DoeZ2kcQEcefbWQ=";
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
        vendorHash = "sha256-u5+mc5UK+AotMcVj/XJTnFGecOZyuJ5i8yrjZhFZr5k=";
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

      homebrew-zsh-completion = prev.stdenvNoCC.mkDerivation {
        name = "brew-zsh-compmletion";
        src = builtins.fetchurl {
          url = outputs.lib.ghRaw {
            owner = "Homebrew";
            repo = "brew";
            rev = "f7d42ae69317274b615369dddeb1c6694250c759";
            path = "completions/zsh/_brew";
          };
          sha256 = "sha256:1mazf005nkidbq74rnzskal02dxryvfl7v3gyyjz1i6g0gv0pmxr";
        };
        dontUnpack = true;
        installPhase = ''
          mkdir $out/
          cp -r $src $out/_brew
          chmod +x $out/_brew
        '';
      };

      mountpoint-s3 =
        let
          mounts3Ref = outputs.lib.flakeLock.mountpoint-s3;
        in
        naersk'.buildPackage {
          src = prev.fetchFromGitHub {
            inherit (mounts3Ref.original) owner repo;
            inherit (mounts3Ref.locked) rev;
            fetchSubmodules = true;
            hash = "sha256-uV0umUoJkYgmjWjv8GMnk5TRRbCCJS1ut3VV1HvkaAw=";
          };
          singleStep = true;
          buildInputs = with prev; [ fuse ];
          nativeBuildInputs = with prev; [
            cmake
            pkg-config
          ];
        };

      opencode =
        let
          pkg = inputs.opencode.packages.${prev.system}.default;
        in
        prev.symlinkJoin {
          name = "opencode-with-completions";
          paths = [ pkg ];
          postBuild = ''
            mkdir -p $out/share/zsh/site-functions
            HOME=$(mktemp -d) SHELL=${prev.lib.getExe prev.zsh} \
              ${prev.lib.getExe pkg} \
              completion > $out/share/zsh/site-functions/_opencode
          '';
        };

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

      stars =
        let
          flakeRef = outputs.lib.flakeLock.stars;
        in
        prev.buildGoModule {
          pname = normalizeName flakeRef.original.repo;
          version = flakeRef.original.ref;
          src = inputs.stars;
          doCheck = false;
          vendorHash = "sha256-wWX0P/xysioCCUS3M2ZIKd8i34Li/ANbgcql3oSE6yc=";
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
            hash = "sha256-Ryr5mFc+StT1d+jBtRsrOzMtyEJf7W1HbMbnC84ps4s=";
          };
        };

      gemini-cli =
        let
          version = builtins.replaceStrings [ "v" ] [ "" ] outputs.lib.flakeLock.gemini-cli.original.ref;
          npmDepsHash = "sha256-1hHPXYgeinK7SxF9yvQBCHYO7H1htnED3ot7wFzHDn0=";
        in
        prev.gemini-cli.overrideAttrs {
          inherit version;
          src = inputs.gemini-cli;
          inherit npmDepsHash;
          npmDeps = prev.fetchNpmDeps {
            src = inputs.gemini-cli;
            hash = npmDepsHash;
          };
        };

      sentry-cli =
        let
          version = outputs.lib.flakeLock.sentry-cli.original.ref;
        in
        prev.sentry-cli.overrideAttrs (old: {
          inherit version;
          src = inputs.sentry-cli;
          cargoDeps = prev.rustPlatform.fetchCargoVendor {
            src = inputs.sentry-cli;
            hash = "sha256-PUQ55pNiLEI5qxykA/j7RsykKJRTUGOGf2JBLacFGBo=";
          };
          buildInputs = old.buildInputs or [ ] ++ [ prev.curl ];
        });

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
        }
      );
    };
}
