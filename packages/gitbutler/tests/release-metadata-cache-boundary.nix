{
  inputs,
  outputs,
  pkgs,
}:
let
  inherit (pkgs) lib;

  baselineVersion = "1.2.3-cache-probe";
  changedVersion = "9.8.7-cache-probe";

  outputsFor =
    releaseVersion:
    outputs
    // {
      lib = outputs.lib // {
        getFlakeVersion =
          packageName:
          if packageName == "gitbutler" then
            "release/${releaseVersion}"
          else
            outputs.lib.getFlakeVersion packageName;
      };
    };

  packageFor =
    releaseVersion:
    pkgs.callPackage ../default.nix {
      inherit inputs;
      outputs = outputsFor releaseVersion;
    };

  crateFor =
    package: crateName:
    package.passthru.cargoNix.workspaceMembers.${crateName}.build.override {
      inherit (package.passthru) crateOverrides;
      features = [ ];
      runTests = false;
    };

  overrideAttrsFor =
    package: crateName:
    package.passthru.crateOverrides.${crateName} {
      inherit crateName;
      version = "0.0.0";
      buildInputs = [ ];
      nativeBuildInputs = [ ];
    };

  baselinePackage = packageFor baselineVersion;
  changedPackage = packageFor changedVersion;
  baselineUnaffected = crateFor baselinePackage "but-error";
  changedUnaffected = crateFor changedPackage "but-error";
  baselineButSource = (overrideAttrsFor baselinePackage "but").src;
  changedButSource = (overrideAttrsFor changedPackage "but").src;

  versionConsumers = [
    "but"
    "but-update"
  ];
  channelConsumers = [
    "but"
    "but-api"
    "but-path"
    "but-update"
    "gitbutler-tauri"
  ];
  channelOnlyConsumers = builtins.filter (
    crateName: !(builtins.elem crateName versionConsumers)
  ) channelConsumers;

  versionMetadataMatches =
    package: expectedVersion:
    builtins.all (
      crateName: (overrideAttrsFor package crateName).VERSION or null == expectedVersion
    ) versionConsumers;
  channelMetadataMatches =
    package:
    builtins.all (
      crateName: (overrideAttrsFor package crateName).CHANNEL or null == "release"
    ) channelConsumers;
  channelOnlyCratesExcludeVersion =
    package:
    builtins.all (crateName: !((overrideAttrsFor package crateName) ? VERSION)) channelOnlyConsumers;

  baselineCli = baselinePackage.passthru.butDrv;
  changedCli = changedPackage.passthru.butDrv;
in
# This deliberately uses a narrow real-Nix evaluation: AST inspection cannot
# establish derivation identity or prove that effective crate overrides retain
# release metadata only for the Rust crates that compile it into their output.
assert lib.assertMsg (baselineUnaffected.drvPath == changedUnaffected.drvPath) ''
  changing only GitButler release metadata changed the unaffected but-error crate:
    ${baselineUnaffected.drvPath}
    ${changedUnaffected.drvPath}
'';
assert lib.assertMsg (baselineButSource.drvPath == changedButSource.drvPath) ''
  changing only GitButler release metadata changed the patched but source:
    ${baselineButSource.drvPath}
    ${changedButSource.drvPath}
'';
assert lib.assertMsg (
  versionMetadataMatches baselinePackage baselineVersion
  && versionMetadataMatches changedPackage changedVersion
) "GitButler VERSION metadata is missing from a compile-time consumer";
assert lib.assertMsg (
  channelMetadataMatches baselinePackage && channelMetadataMatches changedPackage
) "GitButler CHANNEL metadata is missing from a compile-time consumer";
assert lib.assertMsg (
  channelOnlyCratesExcludeVersion baselinePackage && channelOnlyCratesExcludeVersion changedPackage
) "GitButler VERSION metadata leaked into a channel-only consumer";
assert lib.assertMsg (
  baselinePackage.version == baselineVersion
  && changedPackage.version == changedVersion
  && (baselinePackage.drvAttrs.CHANNEL or null) == "release"
  && (changedPackage.drvAttrs.CHANNEL or null) == "release"
) "the final GitButler GUI derivations lost their version/channel metadata";
assert lib.assertMsg (
  (baselineCli.drvAttrs.VERSION or null) == baselineVersion
  && (changedCli.drvAttrs.VERSION or null) == changedVersion
  && (baselineCli.drvAttrs.CHANNEL or null) == "release"
  && (changedCli.drvAttrs.CHANNEL or null) == "release"
) "the final GitButler CLI derivations lost their version/channel metadata";
assert lib.assertMsg (
  baselineCli.drvPath != changedCli.drvPath
) "changing GitButler release metadata did not change the final CLI derivation";
assert lib.assertMsg (
  baselinePackage.drvPath != changedPackage.drvPath
) "changing GitButler release metadata did not change the final GUI derivation";
{
  check = true;
  unaffectedDrvPath = baselineUnaffected.drvPath;
  butSourceDrvPath = baselineButSource.drvPath;
  baseline = {
    cliDrvPath = baselineCli.drvPath;
    guiDrvPath = baselinePackage.drvPath;
    inherit (baselinePackage) version;
  };
  changed = {
    cliDrvPath = changedCli.drvPath;
    guiDrvPath = changedPackage.drvPath;
    inherit (changedPackage) version;
  };
}
