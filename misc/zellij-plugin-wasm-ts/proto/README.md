# Vendored Zellij plugin API protos

These files are copied from `zellij-org/zellij` and used as the source of truth for code generation.

Refresh them with:

```bash
cd misc/zellij-plugin-wasm-ts
bun run sync-proto /path/to/zellij/zellij-utils/src/plugin_api
```

Then regenerate bindings with:

```bash
nix shell nixpkgs#protobuf -c bun run codegen
```
