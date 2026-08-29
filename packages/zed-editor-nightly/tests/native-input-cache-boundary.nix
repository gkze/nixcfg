# Opt-in integration probe; intentionally excluded from the default check graph
# because it evaluates two complete Zed crate2nix package graphs.
# Run from the repository root with:
#
#   nix eval --offline --impure --option allow-import-from-derivation false \
#     --json --file packages/zed-editor-nightly/tests/native-input-cache-boundary.nix
let
  root = ../../..;
  outputs = builtins.getFlake "git+file://${toString root}";
  system = builtins.currentSystem;
  supportedSystems = [
    "aarch64-darwin"
    "x86_64-linux"
  ];
  pkgs = outputs.pkgs.${system};
  inherit (pkgs) lib;

  changedProtobuf = pkgs.protobuf.overrideAttrs (old: {
    env = (old.env or { }) // {
      ZED_CACHE_BOUNDARY_PROBE = "changed";
    };
  });
  changedPkgs = pkgs // {
    protobuf = changedProtobuf;
  };

  packageFor =
    packageSet:
    lib.callPackageWith (packageSet // { pkgs = packageSet; }) ../default.nix {
      inherit (outputs) inputs;
      inherit outputs;
    };

  workspaceCrateFor =
    package: crateName:
    package.passthru.cargoNix.workspaceMembers.${crateName}.build.override {
      inherit (package.passthru) crateOverrides;
      features = [ ];
      runTests = false;
    };

  baselinePackage = packageFor pkgs;
  changedPackage = packageFor changedPkgs;
  # collections sits upstream of the protobuf graph, so its identity is a
  # meaningful negative control rather than merely an unconfigured override.
  baselineNeutral = workspaceCrateFor baselinePackage "collections";
  changedNeutral = workspaceCrateFor changedPackage "collections";
  baselineProto = workspaceCrateFor baselinePackage "proto";
  changedProto = workspaceCrateFor changedPackage "proto";
  baselineZed = baselinePackage.passthru.zedDrv;
  changedZed = changedPackage.passthru.zedDrv;
in
# This deliberately evaluates real package and crate derivations through the
# package passthru seam. AST inspection cannot establish derivation identity or
# prove that changing protoc invalidates only its audited consumers and their
# dependants.
assert builtins.elem system supportedSystems;
assert lib.assertMsg (baselineNeutral.drvPath == changedNeutral.drvPath) ''
  changing only Zed's protobuf input changed the neutral collections crate:
    ${baselineNeutral.drvPath}
    ${changedNeutral.drvPath}
'';
assert lib.assertMsg (baselineProto.drvPath != changedProto.drvPath) ''
  changing Zed's protobuf input did not change the proto consumer:
    ${baselineProto.drvPath}
    ${changedProto.drvPath}
'';
assert lib.assertMsg (baselineZed.drvPath != changedZed.drvPath) ''
  changing Zed's protobuf input did not reach the final Zed derivation:
    ${baselineZed.drvPath}
    ${changedZed.drvPath}
'';
assert lib.assertMsg (baselinePackage.drvPath != changedPackage.drvPath) ''
  changing Zed's protobuf input did not reach the final package:
    ${baselinePackage.drvPath}
    ${changedPackage.drvPath}
'';
{
  check = true;
  baseline = {
    finalDrvPath = baselinePackage.drvPath;
    neutralDrvPath = baselineNeutral.drvPath;
    protoDrvPath = baselineProto.drvPath;
    zedDrvPath = baselineZed.drvPath;
  };
  changed = {
    finalDrvPath = changedPackage.drvPath;
    neutralDrvPath = changedNeutral.drvPath;
    protoDrvPath = changedProto.drvPath;
    zedDrvPath = changedZed.drvPath;
  };
}
