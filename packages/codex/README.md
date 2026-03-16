# Codex crate2nix packaging notes

This package builds Codex with `crate2nix` from the upstream Rust workspace.

## File ownership

### Generated

- `Cargo.nix`
- `crate-hashes.json`

### Hand-maintained

- `default.nix`
  - source preparation for the checked-in workspace layout
  - crate overrides and install/smoke checks
  - final package assembly
- `normalize_cargo_nix.py`
  - package-specific wrapper around `lib/cargo_nix_normalizer.py`

## Current strategy

- build a `patchedSrc` tree with the minimum source surgery needed for Nix
- import checked-in `Cargo.nix` with `rootSrc = patchedSrc`
- keep compatibility shims centralized in `crateOverrides`
- fail evaluation early if the checked-in `Cargo.nix` version no longer matches
  the upstream `codex-cli` crate version

## Regenerating `Cargo.nix`

```bash
nix run .#nixcfg -- ci pipeline crate2nix --write --package codex
```

For the lower-level manual flow:

```bash
nix build --impure --no-link --print-out-paths .#codex.passthru.patchedSrc
crate2nix generate \
  -f "$PATCHED_SRC/Cargo.toml" \
  -o packages/codex/Cargo.nix \
  -h packages/codex/crate-hashes.json \
  --default-features
python packages/codex/normalize_cargo_nix.py packages/codex/Cargo.nix
```

## Recommended validation

```bash
nix run .#nixcfg -- ci pipeline crate2nix --package codex
nix build .#codex --no-link
CODEX=$(nix path-info .#codex)/bin/codex
"$CODEX" --version
"$CODEX" --help >/dev/null
```
