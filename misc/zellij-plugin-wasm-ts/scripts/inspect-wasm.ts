type WasmImport = {
  module: string;
  name: string;
  kind: number;
};

type WasmExport = {
  name: string;
  kind: number;
  index: number;
};

function readU32Leb(bytes: Uint8Array, offset: number): [number, number] {
  let result = 0;
  let shift = 0;
  let cursor = offset;
  while (true) {
    const byte = bytes[cursor++];
    result |= (byte & 0x7f) << shift;
    if ((byte & 0x80) === 0) {
      return [result >>> 0, cursor];
    }
    shift += 7;
  }
}

function readString(bytes: Uint8Array, offset: number): [string, number] {
  const [length, next] = readU32Leb(bytes, offset);
  const slice = bytes.subarray(next, next + length);
  return [new TextDecoder().decode(slice), next + length];
}

function parseWasm(bytes: Uint8Array): { imports: WasmImport[]; exports: WasmExport[] } {
  const magic = String.fromCharCode(...bytes.subarray(0, 4));
  if (magic !== "\0asm") {
    throw new Error("Not a WebAssembly binary");
  }

  const imports: WasmImport[] = [];
  const exports: WasmExport[] = [];

  let offset = 8; // skip magic + version
  while (offset < bytes.length) {
    const sectionId = bytes[offset++];
    const [sectionSize, sectionStart] = readU32Leb(bytes, offset);
    offset = sectionStart;
    const sectionEnd = offset + sectionSize;
    const section = bytes.subarray(offset, sectionEnd);
    offset = sectionEnd;

    if (sectionId === 2) {
      let cursor = 0;
      const [count, next] = readU32Leb(section, cursor);
      cursor = next;
      for (let i = 0; i < count; i += 1) {
        let module: string;
        [module, cursor] = readString(section, cursor);
        let name: string;
        [name, cursor] = readString(section, cursor);
        const kind = section[cursor++];
        imports.push({ module, name, kind });
        switch (kind) {
          case 0: {
            [, cursor] = readU32Leb(section, cursor);
            break;
          }
          case 1: {
            cursor += 1;
            const limits = section[cursor++];
            [, cursor] = readU32Leb(section, cursor);
            if (limits !== 0) {
              [, cursor] = readU32Leb(section, cursor);
            }
            break;
          }
          case 2: {
            const limits = section[cursor++];
            [, cursor] = readU32Leb(section, cursor);
            if (limits !== 0) {
              [, cursor] = readU32Leb(section, cursor);
            }
            break;
          }
          case 3: {
            cursor += 2;
            break;
          }
          default:
            throw new Error(`Unsupported import kind ${kind}`);
        }
      }
    }

    if (sectionId === 7) {
      let cursor = 0;
      const [count, next] = readU32Leb(section, cursor);
      cursor = next;
      for (let i = 0; i < count; i += 1) {
        let name: string;
        [name, cursor] = readString(section, cursor);
        const kind = section[cursor++];
        const [index, nextIndex] = readU32Leb(section, cursor);
        cursor = nextIndex;
        exports.push({ name, kind, index });
      }
    }
  }

  return { imports, exports };
}

const wasmPath = Bun.argv[2];
if (!wasmPath) {
  throw new Error("Usage: bun run scripts/inspect-wasm.ts <path-to-wasm>");
}

const bytes = new Uint8Array(await Bun.file(wasmPath).arrayBuffer());
const parsed = parseWasm(bytes);

console.log("imports:");
for (const entry of parsed.imports) {
  console.log(`  - ${entry.module}.${entry.name} (kind=${entry.kind})`);
}
console.log("exports:");
for (const entry of parsed.exports) {
  console.log(`  - ${entry.name} (kind=${entry.kind}, index=${entry.index})`);
}

const requiredImports = ["zellij.host_run_plugin_command"];
const requiredExports = ["_start", "load", "update", "pipe", "render"];

const importNames = new Set(parsed.imports.map((entry) => `${entry.module}.${entry.name}`));
const exportNames = new Set(parsed.exports.map((entry) => entry.name));

for (const requiredImport of requiredImports) {
  if (!importNames.has(requiredImport)) {
    throw new Error(`Missing required import: ${requiredImport}`);
  }
}
for (const requiredExport of requiredExports) {
  if (!exportNames.has(requiredExport)) {
    throw new Error(`Missing required export: ${requiredExport}`);
  }
}

console.log("ABI smoke test passed.");
