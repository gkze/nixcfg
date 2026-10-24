# Equivalence of the vendored `buildRustCrate` (overlays/_lib/build-rust-crate)
# with nixpkgs' own builder.
#
# The override is only acceptable if it never changes a derivation, and that
# is a property of evaluated derivations (drvPaths and dependency order), so
# an AST check of the vendored file cannot establish it. The graph is kept
# tiny: a synthetic diamond with duplicated transitive dependencies, plus one
# real codex crate (hyper) where the crate2nix graph is available.
{ pkgs }:
let
  inherit (pkgs) lib;
  buildRustCrateOverride = import ../../overlays/_lib/build-rust-crate;
  current = buildRustCrateOverride.isCurrentFor pkgs;

  # A synthetic graph whose closure lists contain repeated entries:
  #   leaf <- left, right <- top, with `leaf` also a direct and build dep of top.
  graphFor =
    builder:
    let
      mk =
        crateName: attrs:
        builder (
          {
            inherit crateName;
            version = "0.1.0";
            src = pkgs.emptyDirectory;
          }
          // attrs
        );
      leaf = mk "leaf" { };
      left = mk "left" { dependencies = [ leaf ]; };
      right = mk "right" { dependencies = [ leaf ]; };
      top = mk "top" {
        dependencies = [
          left
          right
          leaf
        ];
        buildDependencies = [
          right
          leaf
        ];
      };
    in
    {
      inherit top;
      completeDeps = map (drv: drv.outPath) top.completeDeps;
      completeBuildDeps = map (drv: drv.outPath) top.completeBuildDeps;
    };

  vendoredGraph = graphFor (buildRustCrateOverride.vendoredFor pkgs);
  upstreamGraph = graphFor (buildRustCrateOverride.upstreamFor pkgs);

  # codex is a crate2nix package only on its supported systems.
  codexCargoNix = if pkgs ? codex then pkgs.codex.passthru.cargoNix or null else null;
  codexCrateWith =
    builderFor:
    (codexCargoNix.internal.buildRustCrateWithFeatures {
      packageId = "hyper";
      buildRustCrateForPkgsFunc = builderFor;
    }).drvPath;

  checks =
    lib.optionals current [
      (lib.assertMsg (
        vendoredGraph.top.drvPath == upstreamGraph.top.drvPath
      ) "synthetic graph: vendored and upstream drvPaths differ")
      (lib.assertMsg (
        vendoredGraph.completeDeps == upstreamGraph.completeDeps
      ) "synthetic graph: completeDeps order differs")
      (lib.assertMsg (
        vendoredGraph.completeBuildDeps == upstreamGraph.completeBuildDeps
      ) "synthetic graph: completeBuildDeps order differs")
      (lib.assertMsg (
        builtins.length vendoredGraph.completeDeps == 3
      ) "synthetic graph: completeDeps must be deduplicated to left, right, leaf")
      # Equal drvPaths cannot show which builder is wired in; the vendored
      # file's extra `upstreamDir` callPackage argument can.
      (lib.assertMsg (
        lib.functionArgs pkgs.buildRustCrate.override ? upstreamDir
      ) "pkgs.buildRustCrate must resolve to the vendored builder while it is current")
      (lib.assertMsg (
        lib.functionArgs pkgs.buildPackages.buildRustCrate.override ? upstreamDir
      ) "pkgs.buildPackages.buildRustCrate (proc-macros, build scripts) must use it too")
    ]
    ++ lib.optionals (current && codexCargoNix != null) [
      (lib.assertMsg (
        codexCrateWith buildRustCrateOverride.vendoredFor
        == codexCrateWith buildRustCrateOverride.upstreamFor
      ) "codex hyper crate: vendored and upstream drvPaths differ")
    ];
in
# A stale vendored copy is not a test failure: the overlay already falls back
# to upstream and warns. Only a current copy must be equivalent.
builtins.deepSeq checks (
  if current then
    true
  else
    lib.warn "build-rust-crate override is stale; equivalence not checked" true
)
