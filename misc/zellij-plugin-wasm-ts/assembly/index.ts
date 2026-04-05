// Minimal Zellij-compatible plugin skeleton.
//
// This is intentionally tiny. The point of this file is to prove the basic ABI:
// - WASI preview1 module with exported `_start`
// - plugin exports `load`, `update`, `pipe`, `render`
// - custom import from module `zellij`

// @ts-ignore: decorator
@external("zellij", "host_run_plugin_command")
declare function hostRunPluginCommand(): void;

export function load(): void {
  // Later: read plugin configuration payload from stdin and decode via generated bindings.
}

export function update(): i32 {
  // Later: decode a protobuf Event from stdin and return 1 when a render is needed.
  return 1;
}

export function pipe(): i32 {
  // Later: decode a protobuf PipeMessage from stdin.
  return 0;
}

export function render(rows: i32, cols: i32): void {
  // Later: write the rendered string to stdout for Zellij to read.
  let _ignored = rows + cols;
}

// Export a function that references the Zellij import so the generated Wasm
// definitely retains the `zellij.host_run_plugin_command` import.
export function pingHost(): void {
  hostRunPluginCommand();
}
