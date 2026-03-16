# Zed crate2nix packaging notes

This package builds Zed Nightly with `crate2nix` on Darwin.

## File ownership

### Generated

- `Cargo.nix`
- `crate-hashes.json`

### Hand-maintained

- `default.nix`
  - source preparation for Zed's workspace-relative assets and build scripts
  - crate overrides
  - final app bundle assembly
- `normalize_cargo_nix.py`
  - thin package-specific wrapper around `lib/cargo_nix_normalizer.py`

## Why the package is complex

Zed is a large Rust workspace with:

- embedded workspace assets
- multiple path-sensitive build scripts
- generated protobuf code
- a macOS app bundle layout

That means the package needs both crate2nix and a prepared workspace source
tree that preserves Cargo's expectations inside `buildRustCrate`.

## Current strategy

- build a `patchedSrc` tree with the minimal source surgery needed for Nix
- import checked-in `Cargo.nix` with `rootSrc = patchedSrc`
- apply shared per-crate native/build inputs through generated common overrides
- keep only exceptional crates (`gpui_macos`, `tree-sitter`, `webrtc-sys`,
  `zed`, etc.) hand-written
- reuse the upstream Zed app bundle metadata and icon while swapping in the
  crate2nix-built binaries

## Regenerating `Cargo.nix`

Fast path:

```bash
nix run .#nixcfg -- ci pipeline crate2nix --write --package zed-editor-nightly
```

Manual flow:

```bash
nix build --impure --no-link --print-out-paths \
  .#darwinConfigurations.argus.pkgs.zed-editor-nightly.passthru.patchedSrc
```

Save the printed path as `PATCHED_SRC`, then:

```bash
crate2nix generate \
  -f "$PATCHED_SRC/Cargo.toml" \
  -o packages/zed-editor-nightly/Cargo.nix \
  -h packages/zed-editor-nightly/crate-hashes.json \
  --default-features

python packages/zed-editor-nightly/normalize_cargo_nix.py \
  packages/zed-editor-nightly/Cargo.nix
```

## Recommended validation

```bash
nix run .#nixcfg -- ci pipeline crate2nix --package zed-editor-nightly
nix build .#darwinConfigurations.argus.pkgs.zed-editor-nightly --no-link
```

Then smoke-test the CLI:

```bash
ZED=$(nix path-info .#darwinConfigurations.argus.pkgs.zed-editor-nightly)/bin/zed
"$ZED" --help
```

For broader crate2nix guidance in this repo, see
`docs/crate2nix-rust-workspaces.md`.
