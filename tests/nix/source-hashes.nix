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
  unqualifiedEntry = builtins.elemAt source.hashes 0;
  platformEntry = builtins.elemAt source.hashes 1;
  nullPlatformEntry = builtins.elemAt source.hashes 2;
  duplicateEntries = [
    unqualifiedEntry
    (unqualifiedEntry // { hash = "sha256-duplicate"; })
  ];
  updateLib = mkNixcfgLib {
    fakeHashes = true;
    sourceOverrides.demo = source // {
      version = "override";
    };
  };

  inherit (import ./lib.nix) assertEq;

  checks = [
    (assertEq "selector chooses among multiple URLs" platformEntry (
      nixcfgLib.selectSourceHashEntry source.hashes "srcHash" { inherit (platformEntry) url; }
    ))
    (assertEq "selector combines URL and platform filters" platformEntry (
      nixcfgLib.selectSourceHashEntry source.hashes "srcHash" {
        inherit (platformEntry) url platform;
      }
    ))
    (assertEq "selector matches explicit null platform" nullPlatformEntry (
      nixcfgLib.selectSourceHashEntry source.hashes "nullPlatform" { platform = null; }
    ))
    (assertEq "selector returns null for a missing identity" null (
      nixcfgLib.selectSourceHashEntry source.hashes "srcHash" {
        platform = "x86_64-linux";
        inherit (platformEntry) url;
      }
    ))
    (assertEq "selector rejects duplicate exact identities" false
      (builtins.tryEval (
        nixcfgLib.selectSourceHashEntry duplicateEntries "srcHash" { inherit (unqualifiedEntry) url; }
      )).success
    )
    (assertEq "selector rejects an unfiltered ambiguous type" false
      (builtins.tryEval (nixcfgLib.selectSourceHashEntry source.hashes "srcHash" { })).success
    )
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
