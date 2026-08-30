{ inputs }:
_final: prev:
let
  upstream = inputs.neovim-nightly-overlay;
  upstreamInputs = upstream.inputs;
  platformCompat = import ../../lib/pinned-input-platform-compat;
  pkgs =
    platformCompat.withLegacyPlatformAttrs
      upstreamInputs.nixpkgs.legacyPackages.${prev.stdenv.hostPlatform.system};
  inherit (pkgs) lib;
  neovim-dependencies = import (upstream + "/flake/packages/neovim-dependencies.nix") {
    inherit (upstreamInputs) neovim-src;
    inherit lib pkgs;
  };
  tree-sitter = import (upstream + "/flake/packages/tree-sitter.nix") {
    inherit lib pkgs neovim-dependencies;
  };
  neovim = import (upstream + "/flake/packages/neovim.nix") {
    inherit (upstreamInputs) neovim-src;
    inherit
      lib
      pkgs
      neovim-dependencies
      tree-sitter
      ;
  };
  neovim-debug = import (upstream + "/flake/packages/neovim-debug.nix") {
    inherit lib neovim;
    inherit (pkgs) stdenv llvmPackages_latest;
  };
  neovim-developer = import (upstream + "/flake/packages/neovim-developer.nix") {
    inherit lib pkgs neovim-debug;
  };
in
{
  neovim-unwrapped = neovim;
  inherit
    neovim
    neovim-debug
    neovim-developer
    ;
}
