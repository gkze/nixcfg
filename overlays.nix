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

    vimPlugins = prev.vimPlugins.extend (
      _: _: {
        treewalker-nvim = prev.vimUtils.buildVimPlugin {
          pname = normalizeName outputs.lib.flakeLock.treewalker-nvim.original.repo;
          version = inputs.treewalker-nvim.rev;
          src = inputs.treewalker-nvim;
        };
      }
    );
  };
}
