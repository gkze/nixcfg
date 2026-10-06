{ lib }:
{
  # crate2nix calls `(buildRustCrateForPkgs pkgs).override { defaultCrateOverrides
  # = ...; }`. `pkgs.buildRustCrate` is a callPackage functor (a set). A bare
  # `attrs:` lambda is not, so evaluation fails with "expected a set but found a
  # function" on every platform (Update #1235 / #208 wrap). Keep .override and
  # apply Darwin rlib-metadata attrs to each produced derivation.
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
      wrap =
        inner:
        {
          __functor = self: args: applyDarwinRlibMetadata (inner args);
          override = f: wrap (inner.override f);
        };
    in
    wrap builder;
}
