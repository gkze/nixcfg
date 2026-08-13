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
  ];
in
builtins.deepSeq checks true
