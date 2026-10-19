{ lib }:
{
  # crate2nix calls `(buildRustCrateForPkgs pkgs).override { defaultCrateOverrides
  # = ...; }`. `pkgs.buildRustCrate` is a callPackage functor (a set). A bare
  # `attrs:` lambda is not, so evaluation fails with "expected a set but found a
  # function" on every platform (Update #1235 / #208 wrap). Keep .override and
  # apply per-crate policy to each produced derivation.
  wrapBuildRustCrate =
    builder: isDarwin:
    let
      applyDarwinRlibMetadata =
        drv:
        drv.overrideAttrs (
          _old:
          lib.optionalAttrs isDarwin {
            dontStrip = true;
            stripExclude = [ "*.rlib" ];
          }
        );
      linuxZedUnsplit =
        args:
        args
        // lib.optionalAttrs (!isDarwin && (args.crateName or "") == "zed") {
          # crate2nix splits lib+bin crates into outputs out+lib. Linux rustc
          # then records $out inside $lib (rlib dest / rpath / the custom
          # installPhase that only fills $out), and Nix rejects that
          # multi-output cycle (Update validate-x86, rust_zed-1.25.0).
          # extraDerivationAttrs wins over the hardcoded outputs, but
          # outputDev stays [ "lib" ] unless we override it too — otherwise
          # multiple-outputs still assigns outputInclude from empty $lib.
          # Keep Darwin split so already-warmed rust_zed hashes stay put.
          outputs = [ "out" ];
          outputDev = [ "out" ];
        };
      wrap = inner: {
        __functor =
          _self: args:
          applyDarwinRlibMetadata (inner (linuxZedUnsplit args));
        override = f: wrap (inner.override f);
      };
    in
    wrap builder;
}
