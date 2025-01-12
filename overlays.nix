{ inputs, outputs, ... }:
let
  inherit (builtins) replaceStrings;
  normalizeName = s: replaceStrings [ "." "_" ] [ "-" "-" ] s;
in
{
  default = _: prev: {
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

      cargoDeps = oldAttrs.cargoDeps.overrideAttrs (
        prev.lib.const {
          inherit src;
          name = "${oldAttrs.pname}-${version}-vendor.tar.gz";
          outputHash = "sha256-s5nq3/IDF0DnWXlqoTSjyOZfjtce+MzdRMWwUKzw2UE=";
        }
      );
    });

    stars =
      let
        flakeRef = outputs.lib.flakeLock.stars;
      in
      prev.buildGoModule {
        pname = normalizeName flakeRef.original.repo;
        version = flakeRef.original.ref;
        src = inputs.stars;
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
        blink-cmp = prev.vimPlugins.blink-cmp.overrideAttrs {
          src = inputs.blink-cmp;
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
