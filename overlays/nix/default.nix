{ prev, ... }:
let
  skipFlatAppBundlesPatch = prev.writeText "nix-skip-flat-app-bundles-in-optimiser.patch" ''
    diff --git a/src/libstore/optimise-store.cc b/src/libstore/optimise-store.cc
    --- a/src/libstore/optimise-store.cc
    +++ b/src/libstore/optimise-store.cc
    @@ -110,7 +110,7 @@
          See https://github.com/NixOS/nix/issues/1443 and
          https://github.com/NixOS/nix/pull/2230 for more discussion. */

    -    if (std::regex_search(path.string(), std::regex("\\.app/Contents/.+$"))) {
    +    if (std::regex_search(path.string(), std::regex("\\.app/.+$"))) {
            debug("%s is not allowed to be linked in macOS", PathFmt(path));
            return;
        }
  '';
in
{
  nixVersions = prev.nixVersions // {
    git = prev.nixVersions.git.appendPatches [ skipFlatAppBundlesPatch ];
  };
}
