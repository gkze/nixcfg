# Performance-only `buildRustCrate` override. See ./README.md for the
# rationale, the upstream PR intent, and the removal condition.
#
# This file is deliberately not an overlay fragment (it lives under
# overlays/_lib, which fragment discovery skips). lib/package-overlays.nix
# installs `overlay` ahead of the repo overlays, so overlays that build
# crate2nix graphs against `prev` (goose-cli) see it as well as those that use
# `final` (codex, gitbutler, zed-editor-nightly).
let
  # Provenance of ./build-rust-crate.nix: the nixpkgs revision it was vendored
  # from and the sha256 (base16) of pkgs/build-support/rust/build-rust-crate/
  # default.nix at that revision. Kept as data so source identities stay out
  # of handwritten Nix (lib/tests/test_packaging_source_modification_policy.py).
  vendoredBase = builtins.fromJSON (builtins.readFile ./vendored-base.json);
  baseNixpkgsRev = vendoredBase.nixpkgsRev;
  baseHash = vendoredBase.upstreamDefaultNixSha256;

  upstreamDirFor = pkgs: pkgs.path + "/pkgs/build-support/rust/build-rust-crate";

  # The vendored copy is only equivalent to the exact upstream file it was
  # copied from; any upstream change means it may be stale.
  isCurrentFor = pkgs: builtins.hashFile "sha256" (upstreamDirFor pkgs + "/default.nix") == baseHash;

  # Mirror of the `buildRustCrate` call arguments in nixpkgs
  # pkgs/top-level/all-packages.nix, so both builders see the same toolchain.
  callArgsFor =
    pkgs:
    let
      inherit (pkgs) lib stdenv;
      # True if the builder's rustc was built with support for the target.
      targetAlreadyIncluded = lib.elem stdenv.hostPlatform.rust.rustcTarget (
        lib.splitString "," (
          lib.removePrefix "--target=" (
            lib.elemAt (lib.filter (
              f: lib.hasPrefix "--target=" f
            ) pkgs.pkgsBuildBuild.rustc.unwrapped.configureFlags) 0
          )
        )
      );
    in
    lib.optionalAttrs (stdenv.hostPlatform.libc == null) {
      # Some build targets without libc fail to evaluate with a normal stdenv.
      stdenv = pkgs.stdenvNoCC;
    }
    // (
      if targetAlreadyIncluded then
        { inherit (pkgs.pkgsBuildBuild) rustc cargo; }
      else
        { inherit (pkgs.pkgsBuildHost) rustc cargo; }
    );

  # `pkgs.buildRustCrate` built from the vendored file, or `null` when the
  # upstream file no longer matches the vendored base.
  vendoredFor =
    pkgs:
    if isCurrentFor pkgs then
      pkgs.callPackage ./build-rust-crate.nix (callArgsFor pkgs // { upstreamDir = upstreamDirFor pkgs; })
    else
      null;

  # The unmodified nixpkgs builder, called the way all-packages.nix calls it.
  upstreamFor = pkgs: pkgs.callPackage (upstreamDirFor pkgs) (callArgsFor pkgs);
in
{
  inherit
    baseHash
    baseNixpkgsRev
    isCurrentFor
    upstreamFor
    vendoredFor
    ;

  # Fail open: a stale vendored copy must never change build semantics, so
  # fall back to nixpkgs' own builder and say so.
  overlay =
    final: prev:
    let
      vendored = vendoredFor final;
    in
    {
      buildRustCrate =
        if vendored != null then
          vendored
        else
          prev.lib.warn (
            "overlays/_lib/build-rust-crate: nixpkgs build-rust-crate/default.nix no longer "
            + "matches the vendored base (nixpkgs ${baseNixpkgsRev}); using the upstream "
            + "buildRustCrate. Re-vendor or drop the override (see README.md)."
          ) prev.buildRustCrate;
    };
}
