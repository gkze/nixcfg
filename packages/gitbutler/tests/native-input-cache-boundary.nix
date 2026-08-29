# Opt-in integration probe; intentionally excluded from the default check graph
# because it evaluates two complete GitButler crate2nix package graphs.
# Run from the repository root with:
#
#   nix eval --offline --impure --option allow-import-from-derivation false \
#     --json --file packages/gitbutler/tests/native-input-cache-boundary.nix
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

  changedOpenssl = pkgs.openssl.overrideAttrs (old: {
    env = (old.env or { }) // {
      GITBUTLER_CACHE_BOUNDARY_PROBE = "changed";
    };
  });
  changedDefaultCrateOverrides = pkgs.defaultCrateOverrides // {
    openssl-sys =
      attrs:
      (pkgs.defaultCrateOverrides.openssl-sys attrs)
      // {
        buildInputs = [ changedOpenssl ];
      };
  };
  changedPkgs = pkgs // {
    defaultCrateOverrides = changedDefaultCrateOverrides;
    openssl = changedOpenssl;
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
  baselineNeutral = workspaceCrateFor baselinePackage "but-error";
  changedNeutral = workspaceCrateFor changedPackage "but-error";
  baselineCli = baselinePackage.passthru.butDrv;
  changedCli = changedPackage.passthru.butDrv;
in
# This deliberately evaluates real package and crate derivations through the
# package passthru seam. AST inspection cannot establish derivation identity or
# prove that changing a native library invalidates its consumer without also
# invalidating a Rust crate that does not consume it.
assert builtins.elem system supportedSystems;
assert lib.assertMsg (baselineNeutral.drvPath == changedNeutral.drvPath) ''
  changing only GitButler's OpenSSL input changed the neutral but-error crate:
    ${baselineNeutral.drvPath}
    ${changedNeutral.drvPath}
'';
assert lib.assertMsg (baselineCli.drvPath != changedCli.drvPath) ''
  changing GitButler's OpenSSL input did not change the CLI consumer:
    ${baselineCli.drvPath}
    ${changedCli.drvPath}
'';
assert lib.assertMsg (baselinePackage.drvPath != changedPackage.drvPath) ''
  changing GitButler's OpenSSL input did not change the final package:
    ${baselinePackage.drvPath}
    ${changedPackage.drvPath}
'';
{
  check = true;
  baseline = {
    cliDrvPath = baselineCli.drvPath;
    finalDrvPath = baselinePackage.drvPath;
    neutralDrvPath = baselineNeutral.drvPath;
  };
  changed = {
    cliDrvPath = changedCli.drvPath;
    finalDrvPath = changedPackage.drvPath;
    neutralDrvPath = changedNeutral.drvPath;
  };
}
