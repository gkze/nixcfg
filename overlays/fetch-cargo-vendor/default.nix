{ final, prev, ... }:
let
  streamRetryPatch = builtins.toFile "fetch-cargo-vendor-stream-timeout-retry.patch" (
    builtins.readFile ./stream-timeout-retry.patch
  );
  # Nixpkgs keeps this utility private to fetchCargoVendor. Override its writer
  # only inside that fetcher, preserving its Python bootstrap and proxy support.
  writers = final.buildPackages.writers // {
    writePython3Bin =
      name: args: content:
      let
        original = final.buildPackages.writers.writePython3Bin name args content;
      in
      if name != "fetch-cargo-vendor-util" then
        original
      else
        original.overrideAttrs (
          finalAttrs: old: {
            buildCommand = old.buildCommand + ''
              ${final.buildPackages.patch}/bin/patch --fuzz=0 "$out/bin/${name}" < ${streamRetryPatch}
            '';
            passthru = (old.passthru or { }) // {
              tests = (old.passthru.tests or { }) // {
                network = final.buildPackages.runCommand "fetch-cargo-vendor-network-test" { } ''
                  ${old.interpreter} ${../../lib/tests/cargo_vendor_network.py} ${finalAttrs.finalPackage}/bin/${name}
                  touch "$out"
                '';
              };
            };
          }
        );
  };
  # Only the fixed-output download stage needs the network patch. Keep the
  # original offline converter so successful vendor outputs retain their cache
  # identities instead of rebuilding the Rust/Python bootstrap dependency graph.
  wrapFetcher =
    fetcher:
    fetcher
    // {
      __functor =
        _: args:
        let
          original = fetcher args;
          patched = (fetcher.override { inherit writers; }) args;
        in
        original.overrideAttrs { inherit (patched) vendorStaging; };
      override = args: wrapFetcher (fetcher.override args);
    };
  # Factory ownership also covers packages using custom Rust toolchains. Retain
  # the upstream callable attributes and dependency-override behavior.
  wrapMakeRustPlatform =
    factory:
    factory
    // {
      __functor =
        _: args:
        let
          platform = factory args;
        in
        platform
        // platform.overrideScope (
          _: previous: {
            fetchCargoVendor = wrapFetcher previous.fetchCargoVendor;
          }
        );
      override = args: wrapMakeRustPlatform (factory.override args);
    };
in
{
  makeRustPlatform = wrapMakeRustPlatform prev.makeRustPlatform;
}
