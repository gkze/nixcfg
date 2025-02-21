{ inputs, outputs, ... }:
let
  normalizeName = s: builtins.replaceStrings [ "." "_" ] [ "-" "-" ] s;
in
{
  default = _: prev: {
    blink-cmp = inputs.blink-cmp.packages.default;

    homebrew-zsh-completion = prev.stdenvNoCC.mkDerivation {
      name = "brew-zsh-compmletion";
      src = builtins.fetchurl {
        url = outputs.lib.ghRaw {
          owner = "Homebrew";
          repo = "brew";
          rev = "9260c966b1e941e37f1895511a1ee6771124be6b";
          path = "completions/zsh/_brew";
        };
        sha256 = "1b0azwfh578hz0vrj9anqx1blf6cmrm6znyd6my45yydaga6s9d1";
      };
      dontUnpack = true;
      installPhase = ''
        mkdir $out/
        cp -r $src $out/_brew
        chmod +x $out/_brew
      '';
    };

    nh = prev.nh.overrideAttrs (oldAttrs: rec {
      version = outputs.lib.flakeLock.nh.original.ref;
      src = inputs.nh;

      preFixup = ''
        mkdir completions

        for sh in bash zsh fish; do
          $out/bin/nh completions $sh > completions/nh.$sh
        done

        installShellCompletion completions/*
      '';

      cargoDeps = prev.rustPlatform.fetchCargoVendor {
        inherit src;
        hash = "sha256-sE0aNNrnF/l75whiyQ2AeMfTC2w/g5xMDAUxleZkeik=";
      };
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

    trdsql =
      let
        flakeRef = outputs.lib.flakeLock.trdsql;
      in
      prev.buildGoModule {
        pname = normalizeName flakeRef.original.repo;
        version = flakeRef.original.ref;
        src = inputs.trdsql;
        vendorHash = "sha256-PoIa58vdDPYGL9mjEeudRYqPfvvr3W+fX5c+NgRIoLg=";
      };

    vimPlugins = prev.vimPlugins.extend (
      _: _: {
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
