# Override flakelight's auto-import to filter out darwin-only packages on
# non-darwin systems.  Without this, `nix flake check --all-systems` fails
# because nixpkgs' check-meta asserts on meta.platforms mismatches.
#
# When `packages/default.nix` exists, flakelight imports it directly instead
# of auto-importing the directory contents.  flakelight.importDir excludes
# default.nix, so there is no recursion.
{ system, flakelight, ... }:
let
  darwinOnly = [ "conductor" ];
  all = flakelight.importDir ./.;
  isDarwin = builtins.match ".*-darwin" system != null;
in
if isDarwin then all else removeAttrs all darwinOnly
