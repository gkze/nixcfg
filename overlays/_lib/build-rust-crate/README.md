# `buildRustCrate` evaluation-speed override

This directory holds the `buildRustCrate` override. The vendored file is a
performance-only mitigation: it does not change dependency order, and it
fails open to nixpkgs' own `buildRustCrate` when the upstream file moves.
The exported builder then selects a Rust 1.99 built with LLVM 23 and
checks the installed crate.

## Problem

nixpkgs `pkgs/build-support/rust/build-rust-crate/default.nix` computes
`completeDeps` and `completeBuildDeps` for every crate with `lib.unique`.
`lib.unique` is a `foldl'` over `elem`, so it is quadratic in the length of the
transitive dependency list, and the list is recomputed for every crate in the
graph. The crate2nix packages in this repository (`codex`, `gitbutler`,
`goose-cli`, `zed-editor-nightly`) put about 3,400 crates in the host closure,
so this one function dominates their evaluation time.

## Change

`build-rust-crate.nix` is a vendored copy of that single upstream file with two
deltas:

1. `completeDeps` and `completeBuildDeps` use `uniqueDrvs`, an order-preserving
   deduplication keyed by `outPath` through `builtins.genericClosure`.
1. The sibling helpers (`log.nix`, `configure-crate.nix`, `install-crate.nix`,
   `build-crate.nix`) are imported from the unmodified upstream directory
   through `upstreamDir`, so only this file is vendored.

`uniqueDrvs` returns the same list as `lib.unique` for derivations: Nix compares
two derivations with `==` by `outPath`, and `genericClosure` keeps the first
occurrence of each key in start-set order. The elements are always
`lib.getLib` of `buildRustCrate` results, so every element is a derivation with
an `outPath`.

`default.nix` exposes `overlay`, which `lib/package-overlays.nix` installs ahead
of the repository overlays, so `prev.buildRustCrate` (goose-cli) and
`final.buildRustCrate` (codex, gitbutler, zed-editor-nightly through
`wrapBuildRustCrate`) both resolve to it. It calls the vendored file with the
same arguments that nixpkgs `pkgs/top-level/all-packages.nix` uses, including
the `targetAlreadyIncluded` toolchain choice and the `stdenvNoCC` fallback.

## Failure behavior

`vendored-base.json` records the nixpkgs revision and the sha256 of the
upstream `default.nix` the copy was taken from; `default.nix` reads it. When
`builtins.hashFile` of the current upstream file differs, the overlay uses
`prev.buildRustCrate` for the crate graph and emits a `lib.warn`. A stale
copy can therefore cost evaluation time but cannot change dependency order.
Treat the warning as the signal to re-vendor or remove the vendored file.

The overlay always wraps that builder with an output check. `postInstall`
and `postFixup` reject an rlib that does not start with an archive header
or does not contain rustc's `rust-end-file` metadata trailer, a one-byte
`lib/link` other than a newline, and a link file that is not text. Crate
outputs set `dontStrip`, because fixup runs after `postInstall` and a
stripped rlib can keep the archive header while losing the trailer.
Closure gate run 44910664125 substituted `rust_zerocopy-0.8.52-lib` and
`rust_serde_core-1.0.228-lib` from gkze whose signed nars fail the header
check: rustc then saw a bare `+` or `@`, and a later crate reported a
missing dependency whose rlib was the same kind of bytes. Run 45379398481
substituted `rust_crossbeam-utils-0.8.21-lib` whose rlib was an archive and
whose `.rmeta` section started with the metadata header, but the trailer
was gone, so `crossbeam-channel` failed with E0786. The same run's x86_64
gitbutler failed inside `adler32furious` with
`intrinsic signature mismatch for llvm.x86.avx512.vpdpbusd.256`. Rust 1.99
emits LLVM 23 intrinsic signatures and this nixpkgs still builds
`rust_1_99` with `llvmPackages` (LLVM 21). The exported builder uses
`rust_1_99` overridden onto `llvmPackages_23`, including the package sets
`llvmSharedFor` reads as `pkgSet.llvmPackages`. That is the change in
nixpkgs `af7d40097fd91bb31e682f28052a450a0c17e972`. The check and the
compiler both change crate hashes, so those published paths are not
substituted, and a later bad build fails before it can publish a
replacement. `cfg-if` from that cache is a real archive and is unaffected
except for the new hash.

## Evidence

- Base: nixpkgs `ef34387ddd751e1ab8857adf4676492d32eb24ec`, upstream file
  sha256 `3e2716a3f6dba6e500caf57d46518d541a31e57bf917e2b2e16724080b1d03cc`.
- `nix eval --raw .#pkgs.aarch64-darwin.codex.drvPath` on aarch64-darwin, warm
  store, three runs per arm, toggling only the overlay line: CPU time
  10.19–10.32 s without the override and 2.11–2.17 s with it. The drvPath
  stayed `/nix/store/v5nddnsvrly20rinffw8vbakiifmgrff-codex-0.161.0.drv`.
- An earlier whole-host A/B (argus) measured 44.7 s to about 29.8 s of
  evaluation CPU, with all 3,403 Rust crate derivations byte-identical.
- `tests/nix/build-rust-crate-unique-drvs.nix` checks that the vendored builder
  and the upstream builder produce identical drvPaths for a synthetic graph
  with duplicated transitive dependencies and for a real codex crate. The
  output check is outside that comparison. It must change the installed
  builder's drvPath, including after `.override`. The LLVM 23 rustc is also
  outside that comparison: `callArgsFor` still passes nixpkgs' rustc so the
  two graphs stay comparable, and the exported builder replaces it.

## Alternatives considered

- Wait for upstream only: correct long term, but the cost is paid on every
  host evaluation until a fixed nixpkgs reaches this flake.
- Text-substitute the upstream file at evaluation time: rejected by
  `docs/source-modifications.md`, and fragile when upstream reformats.
- `overrideAttrs` on each crate: not possible, because `completeDeps` also
  feeds `configurePhase` inside the same `mkDerivation` call.
- Import-from-derivation patching: rejected; this flake evaluates with IFD
  disabled.

## Upstream

`nixpkgs-build-rust-crate-unique-drvs.patch` is the upstream form of delta 1
against the base revision, intended for a nixpkgs pull request. No pull
request has been opened yet. Record its URL here when one exists.

## Removal condition

The vendored file and its patch can go when the locked nixpkgs contains an
equivalent `uniqueDrvs` fix, or when the `lib.warn` fires and re-vendoring is
not worth it. To re-vendor, copy the new upstream file, reapply the two deltas
above, update `nixpkgsRev` and `upstreamDefaultNixSha256` in
`vendored-base.json`, regenerate the patch, and run the equivalence check.
The LLVM 23 override stays until this nixpkgs builds Rust 1.99 with
`llvmPackages_23` (nixpkgs `af7d40097fd91bb31e682f28052a450a0c17e972`). The
output check stays with the builder: it is what stops a signed, archive-shaped
rlib that rustc cannot decode from being published again.
