# Zellij WASM plugin spike: Bun + TypeScript + AssemblyScript

This directory is a spike for building Zellij plugins from a Bun-managed TypeScript-ish codebase
without forking Zellij.

## What this proves

Validated locally in this repo:

- `bun run build` emits a Wasm module whose imports/exports match Zellij's expectations

- `bun run inspect` confirms the ABI shape

- `zellij action launch-plugin file:... --floating` successfully launches the compiled spike plugin

- Zellij loads **core Wasm modules** with:

  - WASI preview1 linked in
  - a custom import: `zellij.host_run_plugin_command`

- A minimal **AssemblyScript** module can export the Zellij plugin ABI:

  - `_start`
  - `load`
  - `update() -> i32`
  - `pipe() -> i32`
  - `render(rows, cols)`

- Bun can comfortably act as the **orchestrator** for:

  - dependency management
  - code generation
  - compilation
  - ABI inspection / smoke tests

## What this does **not** prove

- Bun does **not** directly compile ordinary TypeScript to a Zellij-compatible Wasm plugin.
- The practical route is:
  1. write plugin code in **AssemblyScript** (a TypeScript-like language that compiles to Wasm)
  1. manage the project with **Bun**
  1. generate protocol bindings from Zellij's `.proto` files

## Source of truth

The vendored `.proto` files in [`./proto`](./proto) were copied from:

- repo: `zellij-org/zellij`
- commit: `d52e1aa2b8fbe7242c448fde677e26cf140f61f9`
- path: `zellij-utils/src/plugin_api/*.proto`

These are enough to generate complete typed message definitions for the plugin API surface.

## Recommended binding strategy

There are really two binding layers to generate:

### 1. Host-side Bun / TypeScript bindings

Generate regular TypeScript for local tooling, tests, harnesses, and protocol inspection:

- generator: `@bufbuild/protoc-gen-es`
- runtime: `@bufbuild/protobuf`
- output: [`generated/ts`](./generated/ts)

This is the nicest layer for:

- smoke tests
- protocol diffing
- fixture generation
- future host-side tooling / harnesses

### 2. Plugin-side AssemblyScript bindings

Generate AssemblyScript message classes for code that actually runs inside Zellij:

- generator: `@stepd/as-proto-gen`
- runtime: `as-proto`
- output: [`assembly/generated`](./assembly/generated)

This is the layer that can eventually back a TypeScript-like SDK analogous to `zellij-tile`.

## Major pieces still to build

This spike only establishes the project shape and codegen path. The remaining big pieces are:

1. **Low-level WASI transport**

   - Zellij plugins exchange protobuf payloads through stdin/stdout as JSON-encoded byte arrays.
   - We still need an AssemblyScript runtime layer for:
     - `readJsonBytesFromStdin()`
     - `writeJsonBytesToStdout()`
     - line-oriented request/response handling

1. **Typed AssemblyScript SDK wrappers**

   - ergonomic wrappers around generated messages, eg:
     - `subscribe(...)`
     - `requestPermission(...)`
     - `dumpLayout(...)`
     - `getPaneInfo(...)`
     - `overrideLayout(...)`
     - `runCommand(...)`
   - this is the equivalent of Zellij's Rust `zellij-tile` shim

1. **AssemblyScript compatibility review**

   - AssemblyScript is TS-like, not full TypeScript
   - generated output may need patching or wrappers around:
     - `Map`
     - strings / byte arrays
     - optional fields / oneofs
     - 64-bit values

1. **End-to-end protocol validation**

   - once transport exists, validate a few real calls against a running Zellij session:
     - `GetFocusedPaneInfo`
     - `Subscribe`
     - `DumpLayout`
     - `OverrideLayout`

1. **Equalize-pane algorithm on top of the SDK**

   - infer split tree from pane geometry and/or dumped layout
   - normalize horizontal / vertical / both axes
   - apply via `OverrideLayout`

## Build flow

```bash
cd misc/zellij-plugin-wasm-ts
bun install
bun run build
bun run inspect
nix shell nixpkgs#protobuf -c bun run codegen
```

## Files

- [`package.json`](./package.json) — Bun-managed project scaffold
- [`asconfig.json`](./asconfig.json) — AssemblyScript config using `@assemblyscript/wasi-shim`
- [`assembly/index.ts`](./assembly/index.ts) — minimal Zellij-compatible plugin skeleton
- [`scripts/inspect-wasm.ts`](./scripts/inspect-wasm.ts) — ABI smoke test for Wasm imports/exports
- [`scripts/codegen.ts`](./scripts/codegen.ts) — generates host TS + AssemblyScript bindings from
  `.proto`
- [`scripts/sync-zellij-protos.ts`](./scripts/sync-zellij-protos.ts) — refreshes vendored `.proto`
  files from a local Zellij checkout

## Why AssemblyScript?

Because it is the most direct route to “Bun + TypeScript + Wasm” for Zellij:

- Bun manages the workspace
- AssemblyScript gives us TypeScript-like syntax compiling to core Wasm
- `@assemblyscript/wasi-shim` gives us WASI-compatible output with `_start`
- custom imports can be declared for `zellij.host_run_plugin_command`

That keeps the final artifact compatible with Zellij's loader while keeping most of the authoring
experience in the TS family.
