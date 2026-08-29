# Zed Nightly crate2nix packaging notes

This package keeps Zed Nightly on the repo's `crate2nix` build path.

It does **not** delegate runtime builds to upstream Zed's coarse-grained Nix
package. The repo-local package expression is responsible for:

- preparing crate-local sources for the crates that need workspace-relative
  assets or packaging patches
- building the actual app from the checked-in `Cargo.nix`
- handling the platform-specific install phases for Darwin and Linux

## File ownership

### Generated

- `Cargo.nix`
- `crate-hashes.json`

### Hand-maintained

- `default.nix`
  - evaluator-visible crate2nix graph rooted at the locked Zed input
  - crate-local source preparation for workspace-relative assets/build scripts
  - shared crate overrides and platform-specific install logic
- `normalize_cargo_nix.py`
  - pure package-specific callback loaded by the central crate2nix normalizer
- `install_zed_nightly_app.sh`
  - Darwin app bundle installer used by the crate2nix build

## Current strategy

- evaluate `Cargo.nix` against the locked Zed input, so package evaluation never
  needs to realize a source-preparation derivation
- preserve crate2nix's per-crate source filtering by preparing only the crates
  that need source surgery; unaffected crates keep their original cache boundary
- retain the full `patchedSrc` tree only for update-time Cargo.nix generation
- keep the checked-in `Cargo.nix` / `crate-hashes.json` refresh flow for update automation
- use one package expression for both Darwin and Linux
- keep `crate2nixSourceOnly` available for CI/update tooling that only needs the prepared workspace source

## Regenerating `Cargo.nix`

Fast path:

```bash
nix run .#nixcfg -- ci pipeline crate2nix --write --package zed-editor-nightly
```

The `.#` form evaluates Git's tracked source set. Stage new or renamed artifacts before invoking it
so Nix can see them.

Manual flow:

```bash
nix build --impure --no-link --print-out-paths \
  .#zed-editor-nightly-crate2nix-src
```

Save the printed path as `PATCHED_SRC`, then:

```bash
crate2nix generate \
  -f "$PATCHED_SRC/Cargo.toml" \
  -o packages/zed-editor-nightly/Cargo.nix \
  -h packages/zed-editor-nightly/crate-hashes.json \
  --default-features

nix run .#nixcfg -- ci pipeline crate2nix normalize zed-editor-nightly
```

## Recommended validation

```bash
nix eval --option allow-import-from-derivation false --raw \
  .#pkgs.aarch64-darwin.zed-editor-nightly.drvPath
nix eval --option allow-import-from-derivation false --raw \
  .#pkgs.x86_64-linux.zed-editor-nightly.drvPath
nix run .#nixcfg -- ci pipeline crate2nix --package zed-editor-nightly
nix build .#pkgs.aarch64-darwin.zed-editor-nightly --no-link
nix build .#pkgs.x86_64-linux.zed-editor-nightly --no-link
```

Then smoke-test the CLI:

```bash
ZED=$(nix path-info .#pkgs.aarch64-darwin.zed-editor-nightly)/bin/zed
"$ZED" --help
```

For broader crate2nix guidance in this repo, see `docs/crate2nix-rust-workspaces.md`.
