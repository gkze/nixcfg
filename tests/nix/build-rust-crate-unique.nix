# The buildRustCrate dedupe override must be invisible in derivations: the
# same crate graph built with upstream lib.unique has to keep identical
# completeDeps order and drvPaths. Comparing drvPaths needs real evaluation
# of buildRustCrate; a tiny synthetic graph keeps that cheap.
{ pkgs }:
let
  inherit (pkgs) lib;

  patched = pkgs.buildRustCrate;
  upstream = pkgs.buildRustCrate.override { inherit lib; };

  mkGraph =
    buildRustCrate:
    let
      crate =
        crateName: dependencies:
        buildRustCrate {
          inherit crateName dependencies;
          version = "0.1.0";
          src = pkgs.emptyDirectory;
        };
      a = crate "a" [ ];
      b = crate "b" [ a ];
      c = crate "c" [
        b
        a
      ];
      # Repeats a and b through several paths so deduplication matters.
      d = crate "d" [
        c
        b
        a
      ];
    in
    {
      inherit d;
      completeDepNames = map (dep: dep.crateName) d.completeDeps;
    };

  patchedGraph = mkGraph patched;
  upstreamGraph = mkGraph upstream;

  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";
in
assert assertEq "upstream completeDeps order" [
  "c"
  "b"
  "a"
] upstreamGraph.completeDepNames;
assert assertEq "patched completeDeps order" upstreamGraph.completeDepNames
  patchedGraph.completeDepNames;
assertEq "patched drvPath" upstreamGraph.d.drvPath patchedGraph.d.drvPath
