{
  pkgs,
  src,
}:
let
  inherit (pkgs) buzz;
  nativeLock = builtins.fromJSON (builtins.readFile (src + "/packages/buzz/native-lock.json"));
  status = buzz.passthru.buzzDesktopCandidateStatus;
  expectedMacApp = {
    bundleId = "xyz.block.buzz.app";
    bundleName = "Buzz.app";
    bundleRelPath = "Applications/Buzz.app";
    installMode = "copy";
  };
in
# AST-only checks cannot prove that the evaluated candidate drvPath/outPath
# still match the updater-owned artifact attestation.
assert buzz.pname == "buzz-desktop-candidate";
assert !(buzz.meta.broken or false);
assert buzz.passthru.buzzBuildGates == [ ];
assert status.wired;
assert status.identity == nativeLock.desktopBundleValidation.candidate;
assert status.evidence == nativeLock.desktopBundleValidation;
assert status.validationComplete;
assert status.exportReady;
assert buzz.passthru.macApp == expectedMacApp;
true
