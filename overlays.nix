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
            nvimSkipModules = [ "aerial.fzf-lua" ];
          };

          octo-nvim = p.octo-nvim.overrideAttrs {
            nvimSkipModules = [
              "octo.pickers.fzf-lua.entry_maker"
              "octo.pickers.fzf-lua.pickers.actions"
              "octo.pickers.fzf-lua.pickers.assigned_labels"
              "octo.pickers.fzf-lua.pickers.assignees"
              "octo.pickers.fzf-lua.pickers.changed_files"
              "octo.pickers.fzf-lua.pickers.commits"
              "octo.pickers.fzf-lua.pickers.gists"
              "octo.pickers.fzf-lua.pickers.issue_templates"
              "octo.pickers.fzf-lua.pickers.issues"
              "octo.pickers.fzf-lua.pickers.labels"
              "octo.pickers.fzf-lua.pickers.notifications"
              "octo.pickers.fzf-lua.pickers.pending_threads"
              "octo.pickers.fzf-lua.pickers.project_cards"
              "octo.pickers.fzf-lua.pickers.project_cards_v2"
              "octo.pickers.fzf-lua.pickers.project_columns"
              "octo.pickers.fzf-lua.pickers.project_columns_v2"
              "octo.pickers.fzf-lua.pickers.prs"
              "octo.pickers.fzf-lua.pickers.repos"
              "octo.pickers.fzf-lua.pickers.review_commits"
              "octo.pickers.fzf-lua.pickers.search"
              "octo.pickers.fzf-lua.pickers.users"
              "octo.pickers.fzf-lua.provider"
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
