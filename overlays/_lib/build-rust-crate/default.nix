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
  # Refuse a crate output that is not what buildRustCrate just wrote.
  #
  # Run 44910664125 substituted rust_zerocopy-0.8.52-lib and
  # rust_serde_core-1.0.228-lib from gkze. Their nars are signed and their
  # narinfos are 200, and the bytes are not a crate: the rlib does not start
  # with an archive header, and lib/link is the single byte "+" or "@".
  # setup_link_paths then passes that byte to rustc twice (LINK and
  # EXTRA_BUILD). half fails with "multiple input filenames" on "+", bitflags
  # fails loading an argument file named "@", and reqwest reports E0463 for
  # tower whose rlib is the same class of garbage. cfg-if from the same cache,
  # whose rlib is a real archive and whose link file is a newline, is fine.
  # The check fails the producing build, so a faulted store cannot publish
  # another one. Wrapping the builder changes every crate hash, so the
  # published paths are not substituted again.
  addCrateOutputCheck =
    drv:
    drv.overrideAttrs (old: {
      postInstall = (old.postInstall or "") + ''
        nixcfg-verify-crate-outputs() {
          local root rlib magic size byte
          for root in "$out" ''${lib:+"$lib"}; do
            if [[ -d $root/lib ]]; then
              for rlib in "$root"/lib/*.rlib; do
                [[ -f $rlib ]] || continue
                # Command substitution strips the trailing newline, so the
                # eight-byte '!<arch>\n' header compares as '!<arch>'.
                magic=$(head -c 8 "$rlib" || true)
                if [[ $magic != '!<arch>'* ]]; then
                  echo "buildRustCrate: corrupt rlib $rlib" >&2
                  exit 1
                fi
              done
              if [[ -f $root/lib/link ]]; then
                size=$(wc -c < "$root/lib/link" | tr -d '[:space:]')
                # A crate with no linker flags installs a single newline.
                # head/command substitution would drop that newline and reject it.
                if [[ $size == 1 ]]; then
                  byte=$(od -An -tu1 "$root/lib/link" | tr -d '[:space:]')
                  if [[ $byte != 10 ]]; then
                    echo "buildRustCrate: corrupt link file $root/lib/link" >&2
                    exit 1
                  fi
                fi
                # grep treats a NUL pattern as empty and ignores NUL in the
                # file. Count the bytes that are neither printable nor space.
                if [[ $(
                  LC_ALL=C tr -d '[:print:][:space:]' < "$root/lib/link" \
                    | wc -c \
                    | tr -d '[:space:]'
                ) != 0 ]]; then
                  echo "buildRustCrate: corrupt link file $root/lib/link" >&2
                  exit 1
                fi
              fi
            fi
          done
        }
        nixcfg-verify-crate-outputs
      '';
    });

  # Keep callPackage's `.override { rustc = ...; }` formals. crate2nix and
  # zed call that attrset override; a bare `f:` lambda drops `upstreamDir`
  # from `lib.functionArgs` and fails the functor check.
  withIntegrityCheck =
    lib: inner:
    let
      wrappedOverride = newArgs: withIntegrityCheck lib (inner.override newArgs);
    in
    {
      __functor = _self: args: addCrateOutputCheck (inner args);
      override = lib.mirrorFunctionArgs inner.override wrappedOverride;
    };

  inherit
    addCrateOutputCheck
    baseHash
    baseNixpkgsRev
    isCurrentFor
    upstreamFor
    vendoredFor
    withIntegrityCheck
    ;

  # The uniqueDrvs copy fails open: a stale vendored file must not change
  # crate graphs, so fall back to nixpkgs' builder. The output check stays
  # either way. A published corrupt path is reused until its hash changes.
  overlay =
    final: prev:
    let
      vendored = vendoredFor final;
      base =
        if vendored != null then
          vendored
        else
          prev.lib.warn (
            "overlays/_lib/build-rust-crate: nixpkgs build-rust-crate/default.nix no longer "
            + "matches the vendored base (nixpkgs ${baseNixpkgsRev}); using the upstream "
            + "buildRustCrate. Re-vendor or drop the override (see README.md)."
          ) prev.buildRustCrate;
    in
    {
      buildRustCrate = withIntegrityCheck prev.lib base;
    };
}
