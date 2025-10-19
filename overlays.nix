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
      beads = prev.buildGoModule {
        name = "beads";
        src = inputs.beads;
        subPackages = [ "cmd/bd" ];
        vendorHash = "sha256-9xtp1ZG7aYXatz02PDTmSRXwBDaW0kM7AMQa1RUau4U=";
        doCheck = false;

        nativeBuildInputs = [ prev.installShellFiles ];
        postInstall = ''
          export HOME=$(mktemp -d)
          $out/bin/bd init
          installShellCompletion --cmd beads \
            --bash <($out/bin/bd completion bash) \
            --fish <($out/bin/bd completion fish) \
            --zsh <($out/bin/bd completion zsh)
        '';
      };

      beads-mcp =
        with inputs;
        let
          pyprojNix = pyproject-nix;
          workspace = uv2nix.lib.workspace.loadWorkspace {
            workspaceRoot = "${beads}/integrations/beads-mcp";
          };
          pySet =
            (prev.callPackage pyprojNix.build.packages {
              python = prev.python313;
            }).overrideScope
              (
                prev.lib.composeManyExtensions [
                  pyproject-build-systems.overlays.default
                  (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
                ]
              );
        in
        (prev.callPackages pyprojNix.build.util { }).mkApplication {
          venv = pySet.mkVirtualEnv "beads-mcp" workspace.deps.all // {
            meta.mainProgram = "beads-mcp";
          };
          package = pySet.beads-mcp;
        };

      blink-cmp = inputs.blink-cmp.packages.default;

      claude-code = prev.claude-code.overrideAttrs { version = "2.0.20"; };

      homebrew-zsh-completion = prev.stdenvNoCC.mkDerivation {
        name = "brew-zsh-compmletion";
        src = builtins.fetchurl {
          url = outputs.lib.ghRaw {
            owner = "Homebrew";
            repo = "brew";
            rev = "37f1e48538e6c00e10ab90f095c55988e259c82f";
            path = "completions/zsh/_brew";
          };
          sha256 = "sha256:1ankljjhbhcfnjvgaz5bh6m132slndwplfhk3ry5214hz71nvlf0";
        };
        dontUnpack = true;
        installPhase = ''
          mkdir $out/
          cp -r $src $out/_brew
          chmod +x $out/_brew
        '';
      };

      # TODO: remove in some near future, random tests were failing
      jujutsu = prev.jujutsu.overrideAttrs { doCheck = false; };

      mdq = naersk'.buildPackage { src = inputs.mdq; };

      mountpoint-s3 =
        let
          mounts3Ref = outputs.lib.flakeLock.mountpoint-s3;
        in
        naersk'.buildPackage {
          src = prev.fetchFromGitHub {
            inherit (mounts3Ref.original) owner;
            inherit (mounts3Ref.original) repo;
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

      # TODO: remove in some near future, random tests were failing
      nodejs_20 = prev.nodejs_20.overrideAttrs { doCheck = false; };

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

      vimPlugins = prev.vimPlugins.extend (
        _: _: {
          vim-bundle-mako = prev.vimUtils.buildVimPlugin {
            pname = normalizeName outputs.lib.flakeLock.vim-bundle-mako.original.repo;
            version = inputs.vim-bundle-mako.rev;
            src = inputs.vim-bundle-mako;
          };
        }
      );
    };
}
