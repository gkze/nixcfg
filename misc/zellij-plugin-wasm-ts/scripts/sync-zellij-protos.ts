import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";

const defaultSource = "/tmp/pi-github-repos/zellij-org/zellij/zellij-utils/src/plugin_api";
const sourceDir = Bun.argv[2] ?? defaultSource;
const destDir = new URL("../proto/", import.meta.url);

await mkdir(destDir, { recursive: true });

const entries = (await readdir(sourceDir)).filter((entry) => entry.endsWith(".proto")).sort();
if (entries.length === 0) {
  throw new Error(`No .proto files found in ${sourceDir}`);
}

for (const entry of entries) {
  const srcPath = join(sourceDir, entry);
  const destPath = new URL(entry, destDir);
  const content = await readFile(srcPath, "utf8");
  await writeFile(destPath, content);
  console.log(`synced ${entry}`);
}

const manifestPath = new URL("source.json", destDir);
const previousManifest = await Bun.file(manifestPath).json().catch(() => ({}));
await writeFile(
  manifestPath,
  `${JSON.stringify({ ...previousManifest, sourceDir, syncedAt: new Date().toISOString() }, null, 2)}\n`,
);

console.log(`Synced ${entries.length} proto files from ${sourceDir}`);
