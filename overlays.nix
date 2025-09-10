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
              "resession.extensions.aerial"
              "aerial"
              "aerial.nav_view"
              "aerial.fzf"
              "aerial.fzf-lua"
              "aerial.actions"
              "aerial.autocommands"
              "aerial.backends.markdown"
              "aerial.backends.init"
              "aerial.backends.treesitter.init"
              "aerial.backends.treesitter.helpers"
              "aerial.backends.treesitter.extensions"
              "aerial.backends.lsp.init"
              "aerial.backends.lsp.callbacks"
              "aerial.backends.lsp.util"
              "aerial.backends.man"
              "aerial.backends.asciidoc"
              "aerial.backends.util"
              "aerial.fold"
              "aerial.highlight"
              "aerial.keymap_util"
              "aerial.config"
              "aerial.layout"
              "aerial.window"
              "aerial.data"
              "aerial.tree"
              "aerial.snacks"
              "aerial.command"
              "aerial.util"
              "aerial.render"
              "aerial.loading"
              "aerial.navigation"
              "aerial.nav_actions"
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
