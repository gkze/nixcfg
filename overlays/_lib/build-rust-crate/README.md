# `buildRustCrate` evaluation-speed override

This directory holds a performance-only mitigation. It does not change build
semantics, and it fails open to nixpkgs' own `buildRustCrate`.

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
`builtins.hashFile` of the current upstream file differs, the overlay returns
`prev.buildRustCrate` unchanged and emits a `lib.warn`. A stale copy can
therefore cost evaluation time but cannot change a derivation. Treat the
warning as the signal to re-vendor or remove this directory.

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
  with duplicated transitive dependencies and for a real codex crate.

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

Delete this directory and its line in `lib/package-overlays.nix` when the
locked nixpkgs contains an equivalent fix, or when the `lib.warn` fires and
re-vendoring is not worth it. To re-vendor, copy the new upstream file, reapply
the two deltas above, update `nixpkgsRev` and `upstreamDefaultNixSha256` in
`vendored-base.json`, regenerate the patch, and run the equivalence check.
