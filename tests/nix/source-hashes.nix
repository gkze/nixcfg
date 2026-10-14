{
  lib,
  src ? ../..,
}:
let
  fixture = ./source-hashes;
  source = builtins.fromJSON (builtins.readFile (fixture + "/packages/demo/sources.json"));
  mkNixcfgLib =
    evaluationContext:
    import (src + "/lib/lib.nix") {
      inherit evaluationContext lib;
      inputs = { };
      outputs = { };
      pkgsFor = { };
      src = fixture;
    };
  nixcfgLib = mkNixcfgLib { };
  updateLib = mkNixcfgLib {
    fakeHashes = true;
    sourceOverrides.demo = source // {
      version = "override";
    };
  };

  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  checks = [
    (assertEq "unqualified source hash entry" (builtins.elemAt source.hashes 0) (
      nixcfgLib.sourceHashEntry "demo" "srcHash"
    ))
    (assertEq "platform source hash entry" (builtins.elemAt source.hashes 1) (
      nixcfgLib.sourceHashEntryForPlatform "demo" "srcHash" "aarch64-darwin"
    ))
    (assertEq "unqualified source hash wrapper" "sha256-unqualified" (
      nixcfgLib.sourceHash "demo" "srcHash"
    ))
    (assertEq "platform source hash wrapper" "sha256-platform" (
      nixcfgLib.sourceHashForPlatform "demo" "srcHash" "aarch64-darwin"
    ))
    (assertEq "explicit null platform is not unqualified" false
      (builtins.tryEval (nixcfgLib.sourceHashEntry "demo" "nullPlatform")).success
    )
    (assertEq "missing unqualified hash fails" false
      (builtins.tryEval (nixcfgLib.sourceHashEntry "demo" "missing")).success
    )
    (assertEq "missing platform hash fails" false
      (builtins.tryEval (nixcfgLib.sourceHashEntryForPlatform "demo" "srcHash" "x86_64-linux")).success
    )
    (assertEq "explicit source override" "override" (updateLib.sourceEntry "demo").version)
    (assertEq "explicit fake hash mode" lib.fakeHash (updateLib.sourceHash "demo" "srcHash"))
    # The fake-hash contract covers hashes only: the update probe still has to
    # fetch the real URL, so fake mode must resolve it exactly as real mode does.
    (assertEq "source url" "https://example.test/demo-src.tar.gz" (
      nixcfgLib.sourceUrl "demo" "srcHash"
    ))
    (assertEq "source url under fake hash mode" "https://example.test/demo-src.tar.gz" (
      updateLib.sourceUrl "demo" "srcHash"
    ))
    (assertEq "missing source url fails" false
      (builtins.tryEval (nixcfgLib.sourceUrl "demo" "nullPlatform")).success
    )
  ];
in
builtins.deepSeq checks true
