{
  inputs,
  outputs,
  pkgs,
}:
let
  inherit (pkgs) lib;

  baselineMetadata = {
    commit = "1111111111111111111111111111111111111111";
    shortRev = "11111111";
  };
  changedMetadata = {
    commit = "2222222222222222222222222222222222222222";
    shortRev = "22222222";
  };

  packageFor =
    metadata:
    pkgs.callPackage ../default.nix {
      inputs = inputs // {
        zed = inputs.zed // {
          inherit (metadata) shortRev;
          rev = metadata.commit;
        };
      };
      inherit outputs;
    };

  crateFor =
    package: crateName:
    package.passthru.cargoNix.workspaceMembers.${crateName}.build.override {
      inherit (package.passthru) crateOverrides;
      runTests = false;
    };

  internalCrateFor =
    package: crateName:
    package.passthru.cargoNix.internal.buildRustCrateWithFeatures {
      packageId = crateName;
      inherit (package.passthru) crateOverrides;
      runTests = false;
    };

  baselinePackage = packageFor baselineMetadata;
  changedPackage = packageFor changedMetadata;
  baselineClient = crateFor baselinePackage "client";
  changedClient = crateFor changedPackage "client";
  baselineAutoUpdate = crateFor baselinePackage "auto_update";
  baselineCli = crateFor baselinePackage "cli";
  changedCli = crateFor changedPackage "cli";
  baselineWebrtcSys = internalCrateFor baselinePackage "webrtc-sys";
  baselineZed = baselinePackage.passthru.zedDrv;
  changedZed = changedPackage.passthru.zedDrv;

  expectedVersion = metadata: "unstable-${metadata.shortRev}";
  expectedUpdateExplanation = "Zed has been installed using Nix. Auto-updates have thus been disabled.";
  commitMetadata = metadata: drv: {
    commit = drv.drvAttrs.ZED_COMMIT_SHA or null;
    expectedCommit = metadata.commit;
  };
  releaseMetadata =
    metadata: drv:
    commitMetadata metadata drv
    // {
      releaseVersion = drv.drvAttrs.RELEASE_VERSION or null;
      expectedReleaseVersion = expectedVersion metadata;
    };
in
# This deliberately uses a narrow real-Nix evaluation: AST inspection cannot
# establish derivation identity or prove that crate overrides preserve the
# release metadata passed to the final Rust consumers.
assert lib.assertMsg (baselineClient.drvPath == changedClient.drvPath) ''
  changing only Zed release metadata changed the unaffected client crate:
    ${baselineClient.drvPath}
    ${changedClient.drvPath}
'';
assert lib.assertMsg (
  !(baselineClient.drvAttrs ? FONTCONFIG_FILE)
  && !(baselineClient.drvAttrs ? LK_CUSTOM_WEBRTC)
  && !(baselineClient.drvAttrs ? ZED_UPDATE_EXPLANATION)
) "the unaffected client crate inherited source-volatile or consumer-specific environment";
assert lib.assertMsg (
  baselineZed.drvAttrs ? FONTCONFIG_FILE
  && !(baselineZed.drvAttrs ? LK_CUSTOM_WEBRTC)
  && !(baselineZed.drvAttrs ? ZED_UPDATE_EXPLANATION)
) "the Zed derivation did not receive exactly its scoped font configuration";
assert lib.assertMsg (
  baselineWebrtcSys.drvAttrs ? LK_CUSTOM_WEBRTC
  && !(baselineWebrtcSys.drvAttrs ? FONTCONFIG_FILE)
  && !(baselineWebrtcSys.drvAttrs ? ZED_UPDATE_EXPLANATION)
) "the webrtc-sys derivation did not receive exactly its scoped WebRTC source";
assert lib.assertMsg (
  (baselineCli.drvAttrs.ZED_UPDATE_EXPLANATION or null) == expectedUpdateExplanation
  && !(baselineCli.drvAttrs ? FONTCONFIG_FILE)
  && !(baselineCli.drvAttrs ? LK_CUSTOM_WEBRTC)
  && (baselineAutoUpdate.drvAttrs.ZED_UPDATE_EXPLANATION or null) == expectedUpdateExplanation
  && !(baselineAutoUpdate.drvAttrs ? FONTCONFIG_FILE)
  && !(baselineAutoUpdate.drvAttrs ? LK_CUSTOM_WEBRTC)
) "the CLI and auto_update derivations did not receive exactly their update explanation";
assert lib.assertMsg (
  (baselineCli.drvAttrs.RELEASE_VERSION or null) == expectedVersion baselineMetadata
  && (baselineCli.drvAttrs.ZED_COMMIT_SHA or null) == baselineMetadata.commit
  && (changedCli.drvAttrs.RELEASE_VERSION or null) == expectedVersion changedMetadata
  && (changedCli.drvAttrs.ZED_COMMIT_SHA or null) == changedMetadata.commit
) "the CLI derivations lost their synthetic release metadata";
assert lib.assertMsg (
  (baselineZed.drvAttrs.ZED_COMMIT_SHA or null) == baselineMetadata.commit
  && (changedZed.drvAttrs.ZED_COMMIT_SHA or null) == changedMetadata.commit
) "the Zed derivations lost their synthetic commit metadata";
assert lib.assertMsg (
  baselineCli.drvPath != changedCli.drvPath
) "changing Zed release metadata did not change the CLI derivation";
assert lib.assertMsg (
  baselineZed.drvPath != changedZed.drvPath
) "changing Zed release metadata did not change the Zed derivation";
{
  check = true;
  unaffectedDrvPath = baselineClient.drvPath;
  baseline = {
    cli = releaseMetadata baselineMetadata baselineCli;
    cliDrvPath = baselineCli.drvPath;
    zed = commitMetadata baselineMetadata baselineZed;
    zedDrvPath = baselineZed.drvPath;
  };
  changed = {
    cli = releaseMetadata changedMetadata changedCli;
    cliDrvPath = changedCli.drvPath;
    zed = commitMetadata changedMetadata changedZed;
    zedDrvPath = changedZed.drvPath;
  };
}
