# Override flakelight's auto-import to filter out darwin-only packages on
# non-darwin systems.  Without this, `nix flake check --all-systems` fails
# because nixpkgs' check-meta asserts on meta.platforms mismatches.
#
# When `packages/default.nix` exists, flakelight imports it directly instead
# of auto-importing the directory contents.  flakelight.importDir excludes
# default.nix, so there is no recursion.
{
  system ? null,
  flakelight,
  ...
}:
let
  discovery = import ../lib/discovery.nix;
  registry = import ./registry.nix { src = ../.; };
  imported = flakelight.importDir ./.;
  crate2nixSourceEntries = discovery.discoverCompanionEntries {
    root = ./.;
    directories = builtins.attrNames imported;
    fileName = "crate2nix-src.nix";
  };
  all = imported // builtins.mapAttrs (_: import) crate2nixSourceEntries.entries;
  systemEval = builtins.tryEval system;
  resolvedSystem =
    if systemEval.success && systemEval.value != null then systemEval.value else "x86_64-linux";
  unsupported = builtins.attrNames all;
  supported = builtins.attrNames (registry.forSystem resolvedSystem);
in
removeAttrs all (
  builtins.filter (name: !(builtins.elem name supported)) unsupported ++ registry.helperEntries
)
