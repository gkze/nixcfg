import { readFile, writeFile } from "node:fs/promises";

const [manifestPath, ...unexpectedArguments] = process.argv.slice(2);
if (manifestPath === undefined || unexpectedArguments.length !== 0) {
  throw new Error("usage: rewrite-sherpa-platform-manifest.mjs <package.json>");
}

const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
if (manifest === null || Array.isArray(manifest) || typeof manifest !== "object") {
  throw new Error("sherpa wrapper manifest must be a JSON object");
}

const expectedFields = {
  name: "sherpa-onnx-node",
  version: "1.12.28",
  main: "sherpa-onnx.js",
};
for (const [field, expected] of Object.entries(expectedFields)) {
  if (manifest[field] !== expected) {
    throw new Error(
      `unexpected sherpa wrapper ${field}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(manifest[field])}`,
    );
  }
}

manifest.name = "sherpa-onnx-darwin-arm64";
manifest.main = "sherpa-onnx.node";
await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
