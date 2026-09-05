{
  pkgs,
  src,
}:
let
  inherit (pkgs) buzz;
  nativeLock = builtins.fromJSON (builtins.readFile (src + "/packages/buzz/native-lock.json"));
  status = buzz.passthru.buzzDesktopCandidateStatus;
  candidateIdentity = {
    derivationPath = builtins.unsafeDiscardStringContext buzz.drvPath;
    outputPath = builtins.unsafeDiscardStringContext buzz.outPath;
  };
  expectedMacApp = {
    bundleId = "xyz.block.buzz.app";
    bundleName = "Buzz.app";
    bundleRelPath = "Applications/Buzz.app";
    installMode = "copy";
  };
in
# Prove that app routing exposes the candidate whose exact path the updater's
# post-persistence derivation validation realizes.
assert buzz.pname == "buzz-desktop-candidate";
assert !(buzz.meta.broken or false);
assert nativeLock.buzz.version == buzz.version;
assert buzz.passthru.buzzBuildGates == [ ];
assert status.wired;
assert status.identity == candidateIdentity;
assert status.exportReady;
assert buzz.passthru.macApp == expectedMacApp;
true
