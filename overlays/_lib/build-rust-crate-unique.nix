# Temporary evaluation-time workaround for nixpkgs buildRustCrate.
#
# buildRustCrate derives completeDeps, completeBuildDeps and
# completePropagatedBuildInputs with lib.unique, which is quadratic in the
# transitive dependency list. For the large crate2nix graphs in this repo that
# dominates evaluation: zed-editor-nightly drops from ~19s to ~4s of CPU and
# codex from ~10s to ~2s with this override, with byte-identical drvPaths.
#
# Nix compares derivations by outPath, so deduplicating derivation lists by
# outPath is exactly lib.unique's equality. builtins.genericClosure keeps the
# first occurrence of each key in list order, so argument order (and therefore
# every crate derivation hash) is unchanged. Lists containing any
# non-derivation value keep upstream lib.unique semantics.
#
# Remove this once nixpkgs buildRustCrate stops using lib.unique for these
# lists.
_final: prev:
let
  inherit (prev) lib;

  uniqueDerivations =
    list:
    map (entry: entry.value) (
      builtins.genericClosure {
        startSet = map (value: {
          key = builtins.unsafeDiscardStringContext value.outPath;
          inherit value;
        }) list;
        operator = _: [ ];
      }
    );

  unique =
    list: if builtins.all lib.isDerivation list then uniqueDerivations list else lib.unique list;
in
{
  buildRustCrate = prev.buildRustCrate.override {
    lib = lib // {
      inherit unique;
    };
  };
}
