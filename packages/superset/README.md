# Superset Desktop Nix Package

This package builds `superset-desktop` from the pinned flake input
`inputs.superset` and a repository-local Bun dependency graph in
`packages/superset/bun.nix`.

## Invariants

- Build source is pinned by `flake.lock` (`inputs.superset`).
- Bun tarball fetches are pinned in `packages/superset/bun.nix`.
- Electron runtime modules are resolved from the externalized module roots in
  `packages/superset/default.nix` and copied transitively.
- Install checks assert that required runtime modules and platform-specific
  `@libsql/*` packages are present in the final output.

## Updating

When `inputs.superset` changes, regenerate `packages/superset/bun.nix`:

```bash
nix build .#superset.passthru.updateScript
./result
```

The script runs `bun2nix` against the pinned upstream source and rewrites
`packages/superset/bun.nix`.

## Validation

Run these checks before landing changes:

```bash
nix build .#superset
nix path-info -S .#superset
nix flake check
```

For Darwin host changes in this repository, also run:

```bash
nh darwin switch --no-nom .
```
