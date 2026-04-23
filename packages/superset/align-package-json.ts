import type { PackageJson } from "type-fest";
import bunLock from "./bun.lock";

type PackageJsonWithFlatResolutions = PackageJson & {
  resolutions?: Record<string, string>;
  overrides?: Record<string, string>;
};

const path = new URL("./package.json", import.meta.url);
const pkg = (await Bun.file(path).json()) as PackageJsonWithFlatResolutions;

pkg["resolutions" in pkg ? "resolutions" : "overrides"] = {
  ...(pkg.resolutions ?? pkg.overrides ?? {}),
  ...Object.fromEntries(Object.entries(bunLock.overrides ?? {})),
};

await Bun.write(path, `${JSON.stringify(pkg, null, 2)}\n`);
