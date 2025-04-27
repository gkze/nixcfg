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
          rev = "5fb70f5a5795bd7c29ea0e136bac5ba2d471729a";
          path = "completions/zsh/_brew";
        };
        sha256 = "sha256:1b8wjkdmqrwip97hmfhq3fckqpkanps1335795srr9r7rhq3d6mm";
      };
      dontUnpack = true;
      installPhase = ''
        mkdir $out/
        cp -r $src $out/_brew
        chmod +x $out/_brew
      '';
    };

    mdq = (prev.callPackage inputs.naersk { }).buildPackage { src = inputs.mdq; };

    nh = prev.nh.overrideAttrs rec {
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
        hash = "sha256-MqvYDCtj6omYpwhKvWkI5CRz8ZpT8OLj7SazJUzVtc8=";
      };
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
