# Keep pinned inputs on their validated revisions while presenting the legacy
# stdenv platform attributes they still expect. Repository-owned code should
# continue to use stdenv.hostPlatform directly.
let
  withLegacyPlatformAttrs =
    pkgs:
    pkgs
    // {
      stdenv = pkgs.stdenv // {
        isDarwin = pkgs.stdenv.hostPlatform.isDarwin;
        isLinux = pkgs.stdenv.hostPlatform.isLinux;
      };
    };

  withLegacyPlatformCallPackage =
    pkgs:
    let
      compatPkgs = (withLegacyPlatformAttrs pkgs) // {
        callPackage = pkgs.lib.callPackageWith compatPkgs;
      };
    in
    compatPkgs;
in
{
  inherit
    withLegacyPlatformAttrs
    withLegacyPlatformCallPackage
    ;

  overlay = _final: prev: {
    inherit ((withLegacyPlatformAttrs prev)) stdenv;
  };
}
