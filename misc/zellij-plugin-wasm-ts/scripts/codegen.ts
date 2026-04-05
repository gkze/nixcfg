import { mkdir, readdir } from "node:fs/promises";
import { join } from "node:path";

const cwd = new URL("..", import.meta.url);
const root = Bun.fileURLToPath(cwd);
const protoDir = join(root, "proto");
const tsOutDir = join(root, "generated", "ts");
const asOutDir = join(root, "assembly", "generated");
const esPlugin = join(root, "node_modules", ".bin", "protoc-gen-es");
const asPlugin = join(root, "node_modules", ".bin", "as-proto-gen");
const protoc = Bun.which("protoc") ?? process.env.PROTOC;

if (!protoc) {
  throw new Error("protoc not found. Try: nix shell nixpkgs#protobuf -c bun run codegen");
}

const protoFiles = (await readdir(protoDir))
  .filter((entry) => entry.endsWith(".proto"))
  .sort()
  .map((entry) => join(protoDir, entry));

if (protoFiles.length === 0) {
  throw new Error(`No .proto files found in ${protoDir}`);
}

await mkdir(tsOutDir, { recursive: true });
await mkdir(asOutDir, { recursive: true });

const run = async (args: string[]) => {
  const proc = Bun.spawn(args, {
    cwd: root,
    stdout: "inherit",
    stderr: "inherit",
    stdin: "ignore",
  });
  const exitCode = await proc.exited;
  if (exitCode !== 0) {
    throw new Error(`Command failed (${exitCode}): ${args.join(" ")}`);
  }
};

await run([
  protoc,
  "-I",
  protoDir,
  `--plugin=protoc-gen-es=${esPlugin}`,
  `--es_out=target=ts:${tsOutDir}`,
  ...protoFiles,
]);

await run([
  protoc,
  "-I",
  protoDir,
  `--plugin=protoc-gen-as=${asPlugin}`,
  "--as_opt=gen-helper-methods",
  `--as_out=${asOutDir}`,
  ...protoFiles,
]);

console.log(`Generated ${protoFiles.length} proto files into:`);
console.log(`  - ${tsOutDir}`);
console.log(`  - ${asOutDir}`);
