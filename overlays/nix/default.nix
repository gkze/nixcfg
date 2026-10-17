# Patches the system Nix (`nixVersions.git`, consumed by modules/common.nix
# and modules/home/base.nix) so `nix store optimise` / `auto-optimise-store`
# also skips "flat" `.app` bundles (`Foo.app/_CodeSignature/...`, no
# `Contents/` directory) on macOS.
#
# Why: upstream skips only `\.app/Contents/.+$` (introduced for
# https://github.com/NixOS/nix/issues/1443 by
# https://github.com/NixOS/nix/pull/2230), because macOS refuses to hard-link
# some files inside app bundles. Flat bundles in the store hit the same EPERM
# and abort optimisation. The same failure is reported at
# https://github.com/DeterminateSystems/nix-src/issues/431. No NixOS/nix fix
# PR was found as of 2026-10; recent upstream optimise-store.cc revisions
# still use the `Contents/` regex.
#
# Cost: a patched Nix is never in cache.nixos.org, so every nixpkgs bump
# rebuilds it from source. The rebuilt path is realized by the Darwin host
# roots in `checks.<system>.root-closures` and uploaded by the CI cachix
# daemon (.github/actions/update-runtime, `useDaemon: true`).
#
# Darwin only: the patched code sits under `#ifdef __APPLE__`, so on Linux
# the patch changed nothing but the store path and forced a source build.
#
# Remove when upstream Nix skips non-`Contents/` bundle paths or tolerates
# hard-link failures in optimise-store (check src/libstore/optimise-store.cc),
# or when the patch no longer applies to `nixVersions.git`.
{ prev, ... }:
prev.lib.optionalAttrs prev.stdenv.hostPlatform.isDarwin {
  nixVersions = prev.nixVersions // {
    git = prev.nixVersions.git.appendPatches [ ./skip-flat-app-bundles-in-optimiser.patch ];
  };
}
