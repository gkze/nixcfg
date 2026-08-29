{
  pkgs,
  src,
}:
let
  helper = import (src + "/lib/crate2nix-source-slice.nix");
  fixtureRoot = src + "/tests/nix/fixtures/crate2nix-source-slice";
  rootA = fixtureRoot + "/root-a";
  rootB = fixtureRoot + "/root-b";
  relativePath = "crates/demo";
  sliceName = "crate2nix-source-slice-demo";

  sourceFilter =
    name: type:
    let
      baseName = builtins.baseNameOf (builtins.toString name);
    in
    baseName != "ignored.txt" && !(type == "directory" && baseName == "ignored-dir");

  materialize =
    rootSrc:
    helper.materialize {
      inherit rootSrc sourceFilter;
      sources.${relativePath}.name = sliceName;
    };
  sliceA = (materialize rootA).${relativePath};
  sliceB = (materialize rootB).${relativePath};
  # Independent NAR hash for the filtered fixture. `sourceFor` must reproduce
  # this content-addressed path instead of deriving its own expected value.
  sliceHash = "sha256-FQzl7ksYpS8DC6iyCWq/M3SS8TohyKp1ubdRCJarDhE=";

  sourceIdentity = {
    cargoNixSha256 = "cargo-a";
    input = "fixture";
    narHash = "sha256-input-a=";
    subdir = ".";
  };
  sourceInfo = {
    source = sourceIdentity;
    slices.${relativePath} = {
      hash = sliceHash;
      name = sliceName;
    };
  };
  sourceFor = helper.sourceFor {
    rootSrc = rootA;
    source = sourceIdentity;
    inherit sourceInfo;
  };
  expectedSlice = sourceFor sourceFilter relativePath;

  staleSourceResult =
    staleSource:
    builtins.tryEval (
      builtins.toString (
        (helper.sourceFor {
          rootSrc = rootA;
          source = staleSource;
          inherit sourceInfo;
        })
          sourceFilter
          relativePath
      )
    );
  staleSourceResults = [
    (staleSourceResult (sourceIdentity // { cargoNixSha256 = "cargo-b"; }))
    (staleSourceResult (sourceIdentity // { input = "other-input"; }))
    (staleSourceResult (sourceIdentity // { narHash = "sha256-input-b="; }))
    (staleSourceResult (sourceIdentity // { subdir = "nested"; }))
  ];
  missingSliceResult = builtins.tryEval (
    builtins.toString (sourceFor sourceFilter "vendor/v8-goose-src")
  );
  missingHashResult = builtins.tryEval (
    builtins.toString (
      (helper.sourceFor {
        rootSrc = rootA;
        source = sourceIdentity;
        sourceInfo = {
          source = sourceIdentity;
          slices.${relativePath}.name = sliceName;
        };
      })
        sourceFilter
        relativePath
    )
  );
  capturedHashedSlice =
    (helper.sourceFor {
      rootSrc = rootA;
      source = sourceIdentity;
      inherit sourceInfo;
      materializePath = attributes: attributes;
    })
      sourceFilter
      relativePath;

  rustyV8 = import (src + "/lib/rusty-v8.nix") { inherit (pkgs) lib; };
  v8CrateOverride = rustyV8.mkRustyV8CrateOverride {
    inherit pkgs;
    nativeDrv = null;
    patchedSrc = "patched-source";
  };
  generatedV8Attrs = {
    nativeBuildInputs = [ ];
    src = sourceFor sourceFilter "vendor/v8-goose-src";
  };
  overriddenV8Attrs = generatedV8Attrs // v8CrateOverride generatedV8Attrs;
  overriddenV8SourceResult = builtins.tryEval (builtins.toString overriddenV8Attrs.src);
in
# These are evaluator semantics: AST inspection cannot prove that builtins.path
# reuses an identical content-addressed outPath or remains lazy when the
# production V8 crate override replaces an exempt source slice. The derivation
# below owns the materialized paths and verifies filtered contents at build time.
assert builtins.toString sliceA == builtins.toString sliceB;
assert builtins.toString expectedSlice == builtins.toString sliceA;
assert builtins.all (result: !result.success) staleSourceResults;
assert !missingSliceResult.success;
assert !missingHashResult.success;
assert capturedHashedSlice.sha256 == sliceHash;
assert overriddenV8SourceResult.success;
assert overriddenV8SourceResult.value == "patched-source";
pkgs.runCommand "test-nix-crate2nix-source-slice" { } ''
  test -f ${sliceA}/Cargo.toml
  test ! -e ${sliceA}/ignored.txt
  test ! -e ${sliceA}/ignored-dir
  touch "$out"
''
