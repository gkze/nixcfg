# Zed Nightly crate2nix packaging notes

This package keeps Zed Nightly on the repo's `crate2nix` build path.

It does **not** delegate runtime builds to upstream Zed's coarse-grained Nix
package. The repo-local package expression is responsible for:

- preparing a patched workspace source tree for `crate2nix`
- building the actual app from the checked-in `Cargo.nix`
- handling the platform-specific install phases for Darwin and Linux

## File ownership

### Generated

- `Cargo.nix`
- `crate-hashes.json`

### Hand-maintained

- `default.nix`
  - source preparation for workspace-relative assets/build scripts
  - shared crate overrides and platform-specific install logic
- `normalize_cargo_nix.py`
  - thin package-specific wrapper around `lib/cargo_nix_normalizer.py`
- `install_zed_nightly_app.sh`
  - Darwin app bundle installer used by the crate2nix build

## Current strategy

- build a `patchedSrc` tree with the source surgery needed by `crate2nix`
- keep the checked-in `Cargo.nix` / `crate-hashes.json` refresh flow for update automation
- use one package expression for both Darwin and Linux
- keep `crate2nixSourceOnly` available for CI/update tooling that only needs the prepared workspace source

## Regenerating `Cargo.nix`

Fast path:

```bash
nix run path:.#nixcfg -- ci pipeline crate2nix --write --package zed-editor-nightly
```

Manual flow:

```bash
nix build --impure --no-link --print-out-paths \
  path:.#zed-editor-nightly-crate2nix-src
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
nix run path:.#nixcfg -- ci pipeline crate2nix --package zed-editor-nightly
nix build .#pkgs.aarch64-darwin.zed-editor-nightly --no-link
nix build .#pkgs.x86_64-linux.zed-editor-nightly --no-link
```

Then smoke-test the CLI:

```bash
ZED=$(nix path-info .#pkgs.aarch64-darwin.zed-editor-nightly)/bin/zed
"$ZED" --help
```

For broader crate2nix guidance in this repo, see `docs/crate2nix-rust-workspaces.md`.
