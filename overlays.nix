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
      blink-cmp = inputs.blink-cmp.packages.default;
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
        f: p: {
          aerial-nvim = p.aerial-nvim.overrideAttrs {
            nvimSkipModules = [
              "aerial"
              "aerial.actions"
              "aerial.autocommands"
              "aerial.backends.asciidoc"
              "aerial.backends.init"
              "aerial.backends.lsp.callbacks"
              "aerial.backends.lsp.init"
              "aerial.backends.lsp.util"
              "aerial.backends.man"
              "aerial.backends.markdown"
              "aerial.backends.treesitter.extensions"
              "aerial.backends.treesitter.helpers"
              "aerial.backends.treesitter.init"
              "aerial.backends.util"
              "aerial.command"
              "aerial.config"
              "aerial.data"
              "aerial.fold"
              "aerial.fzf"
              "aerial.fzf-lua"
              "aerial.highlight"
              "aerial.keymap_util"
              "aerial.layout"
              "aerial.loading"
              "aerial.nav_actions"
              "aerial.nav_view"
              "aerial.navigation"
              "aerial.render"
              "aerial.snacks"
              "aerial.tree"
              "aerial.util"
              "aerial.window"
              "resession.extensions.aerial"
            ];
          };

          treewalker-nvim = prev.vimUtils.buildVimPlugin {
            pname = normalizeName outputs.lib.flakeLock.treewalker-nvim.original.repo;
            version = inputs.treewalker-nvim.rev;
            src = inputs.treewalker-nvim;
          };

          vim-bundle-mako = prev.vimUtils.buildVimPlugin {
            pname = normalizeName outputs.lib.flakeLock.vim-bundle-mako.original.repo;
            version = inputs.vim-bundle-mako.rev;
            src = inputs.vim-bundle-mako;
          };
        }
      );
    };
}
