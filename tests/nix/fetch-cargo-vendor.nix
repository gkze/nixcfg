{ pkgs }:
let
  vendorArgs = {
    name = "fetch-cargo-vendor-contract";
    # Only inspect dependencies and output identity; no source/vendor build.
    src = builtins.toFile "Cargo.lock" "";
    hash = pkgs.lib.fakeHash;
  };
  utilityIn =
    deps:
    pkgs.lib.findFirst (package: package.name == "fetch-cargo-vendor-util") null deps.nativeBuildInputs;
  utilityFor = platform: utilityIn (platform.fetchCargoVendor vendorArgs).vendorStaging;
  toolchain = {
    inherit (pkgs.rustPlatform.rust) cargo rustc;
  };
  custom = pkgs.makeRustPlatform toolchain;
  overridden = (pkgs.makeRustPlatform.override { }) toolchain;
  withoutAliases =
    (pkgs.makeRustPlatform.override {
      config = pkgs.config // {
        allowAliases = false;
      };
    })
      toolchain;
  extended = pkgs.rustPlatform.overrideScope (_: _: { networkTestMarker = true; });
  utility = utilityFor pkgs.rustPlatform;
  # A patch to the offline converter would change every vendor output and
  # invalidate cached Rust/Python bootstrap packages. Only patch the FOD stage.
  upstreamFetcher = pkgs.buildPackages.callPackage (
    pkgs.path + "/pkgs/build-support/rust/fetch-cargo-vendor.nix"
  ) { inherit (toolchain) cargo; };
  original = upstreamFetcher vendorArgs;
  patched = pkgs.rustPlatform.fetchCargoVendor vendorArgs;
in
assert original.outPath == patched.outPath;
assert original.vendorStaging.outPath == patched.vendorStaging.outPath;
assert toString (utilityIn original) == toString (utilityIn patched);
assert toString (utilityIn original.vendorStaging) != toString utility;
assert builtins.attrNames custom == builtins.attrNames pkgs.rustPlatform;
assert builtins.attrNames overridden == builtins.attrNames pkgs.rustPlatform;
assert (custom ? fetchCargoTarball) == pkgs.config.allowAliases;
assert !(withoutAliases ? fetchCargoTarball);
assert extended.networkTestMarker;
assert builtins.all (platform: toString (utilityFor platform) == toString utility) [
  custom
  overridden
  withoutAliases
  extended
];
utility.tests.network
