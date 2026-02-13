______________________________________________________________________

## name: nix-add-package description: Add a new package to this nixcfg flake using the correct pattern based on source type (flake input, non-flake source, or external binary) license: MIT compatibility: opencode metadata: audience: maintainers workflow: nix

## What I do

Guide you through adding a new package to this nixcfg repository. There are three main patterns, each with sub-variants. I help you choose the right one and walk you through every file that needs changes.

## When to use me

Use this when you want to add a new package, tool, or application to the Nix configuration. Ask clarifying questions if unsure which pattern to apply.

## Repository Architecture

This repo uses **flakelight** with `nixDir = ./.` and a vertical per-package layout:

- **`packages/`** — Flakelight `callPackage` convention. Each file returns a **single derivation**. Flakelight auto-discovers these, calls them via `callPackage`, and adds them to both `packages.<system>` output and the overlay.
- **`overlays/`** — For things that need `final`/`prev` (overriding existing nixpkgs packages, nested overrides, multi-attr returns). `overlays/default.nix` auto-imports all `overlays/<name>/default.nix` fragments.
- **Per-package `sources.json`** — Each package that needs hashes stores them in `<dir>/<name>/sources.json` as a bare entry (not wrapped in a name key).
- **Per-package `updater.py`** — Each package that needs a custom updater stores it in `<dir>/<name>/updater.py`. Reusable base classes live in `update/updaters/`.

## Decision tree

1. **Is the source a Nix flake that exports packages or overlays?** -> Pattern 1
1. **Is the source a Git repo (flake=false) you build from source?** -> Pattern 2
1. **Is the source a pre-built binary, .dmg, AppImage, or rolling-release app?** -> Pattern 3

### Choosing `packages/` vs `overlays/`

- **`packages/<name>.nix`** — Use when the package is a standalone new derivation that doesn't need `final`/`prev` overlay semantics. The file receives `callPackage` args (e.g., `{ lib, stdenv, fetchurl, mkGoCliPackage, ... }:`).
- **`overlays/<name>/default.nix`** — Use when you need `final`/`prev` to override an existing nixpkgs package, access `inputs.*`, return multiple attrs, or use builders that reference `sources`/`outputs`.

______________________________________________________________________

## Pattern 1: Flake input with packages/overlays

For upstream flakes that export `packages.<system>.default` or `overlays.default`.

### Files to modify

#### `flake.nix` - Add the input

```nix
# In inputs = { ... }:
my-package = {
  url = "github:owner/repo";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

#### Option A: Use upstream overlay (preferred if available)

In `flake.nix`, add to `withOverlays`:

```nix
withOverlays = [
  # ... existing overlays ...
  inputs.my-package.overlays.default
];
```

No changes to `overlays/default.nix` needed.

#### Option B: Direct package assignment in overlay fragment

Create `overlays/my-package/default.nix`:

```nix
{ inputs, system, ... }:
{
  my-package = inputs.my-package.packages.${system}.default;
}
```

### Checklist

- [ ] Add flake input with `inputs.nixpkgs.follows = "nixpkgs"`
- [ ] Either add overlay to `withOverlays` OR create overlay fragment
- [ ] Run `nix flake lock --update-input my-package` to populate lock
- [ ] No `sources.json` or updater changes needed

______________________________________________________________________

## Pattern 2: Non-flake source input (build from source)

For Git repos where you write a derivation. The input has `flake = false;`.

### Files to modify

#### `flake.nix` - Add non-flake input

```nix
# With a version tag:
my-tool = {
  url = "github:owner/repo/v1.2.3";
  flake = false;
};

# Or tracking a branch (no version):
my-tool = {
  url = "github:owner/repo";
  flake = false;
};
```

#### Option A: `packages/my-tool.nix` (standalone callPackage derivation)

Best for Go CLIs, Python packages, Rust crates — any standalone derivation:

```nix
{
  lib,
  mkGoCliPackage,
  inputs,
  ...
}:
mkGoCliPackage {
  pname = "my-tool";
  input = inputs.my-tool;
  subPackages = [ "cmd/my-tool" ];
  cmdName = "my-tool";
  meta = with lib; {
    description = "Description";
    homepage = "https://github.com/owner/repo";
    license = licenses.mit;
  };
}
```

Requires `vendorHash` in per-package `sources.json` (see below).

#### Option B: `overlays/my-tool/default.nix` (overlay fragment)

Best for overriding existing nixpkgs packages or when you need `final`/`prev`:

##### Go override (existing nixpkgs package)

```nix
{ inputs, outputs, prev, slib, ... }:
{
  my-tool = prev.my-tool.overrideAttrs {
    version = slib.getFlakeVersion "my-tool";
    src = inputs.my-tool;
    vendorHash = slib.sourceHash "my-tool" "vendorHash";
  };
}
```

##### Cargo/Rust override

```nix
{ inputs, prev, slib, ... }:
{
  my-tool = prev.my-tool.overrideAttrs (old: {
    version = slib.getFlakeVersion "my-tool";
    src = inputs.my-tool;
    cargoDeps = prev.rustPlatform.fetchCargoVendor {
      src = inputs.my-tool;
      hash = slib.sourceHash "my-tool" "cargoHash";
    };
  });
}
```

##### npm/Node override

```nix
{ inputs, prev, slib, ... }:
let
  version = slib.getFlakeVersion "my-tool";
  npmDepsHash = slib.sourceHash "my-tool" "npmDepsHash";
  npmDeps = prev.fetchNpmDeps {
    src = inputs.my-tool;
    hash = npmDepsHash;
  };
in
{
  my-tool = prev.my-tool.overrideAttrs (old: {
    inherit version npmDepsHash npmDeps;
    src = inputs.my-tool;
  });
}
```

##### Python (uv2nix)

```nix
{ inputs, prev, final, ... }:
{
  my-tool = final.mkUv2nixPackage {
    name = "my-tool";
    src = inputs.my-tool;
    mainProgram = "my-tool";
  };
}
```

No sources.json entry needed (uv2nix handles deps).

##### Vim plugin

```nix
# Inside overlays/default.nix vimPlugins extend block:
my-plugin = prev.vimUtils.buildVimPlugin {
  pname = "my-plugin";
  version = inputs.my-plugin.rev;
  src = inputs.my-plugin;
};
```

#### Per-package `sources.json` (if needed)

Only for Go (`vendorHash`), Cargo (`cargoHash`), npm (`npmDepsHash`), Deno (`denoDepsHash`), or bun (`nodeModulesHash`) builds. Create `packages/my-tool/sources.json` or `overlays/my-tool/sources.json` as a **bare entry**:

```json
{
  "hashes": [
    {
      "hash": "sha256-PLACEHOLDER",
      "hashType": "vendorHash"
    }
  ],
  "input": "my-tool"
}
```

To compute the actual hash, temporarily use `lib.fakeHash` and build to get the expected hash from the error output, or run `nixcfg update my-tool`.

Note: If using `packages/my-tool.nix` (a single file, not a directory), you'll need to create `packages/my-tool/` as a directory instead and rename the nix file to `packages/my-tool/default.nix` so the `sources.json` can be co-located.

#### Per-package `updater.py` (if needed)

Create `packages/my-tool/updater.py` or `overlays/my-tool/updater.py`:

```python
# Go:
from lib.update.updaters.base import go_vendor_updater
go_vendor_updater("my-tool")

# Cargo:
from lib.update.updaters.base import cargo_vendor_updater
cargo_vendor_updater("my-tool")

# npm:
from lib.update.updaters.base import npm_deps_updater
npm_deps_updater("my-tool")

# Deno:
from lib.update.updaters.base import deno_deps_updater
deno_deps_updater("my-tool")

# Bun:
from lib.update.updaters.base import bun_node_modules_updater
bun_node_modules_updater("my-tool")
```

### Checklist

- [ ] Add flake input with `flake = false;` (and version tag if applicable)
- [ ] Add derivation in `packages/` or `overlays/` as appropriate
- [ ] If Go/Cargo/npm/Deno/Bun: add `sources.json` in the package directory
- [ ] If Go/Cargo/npm/Deno/Bun: add `updater.py` in the package directory
- [ ] Run `nix flake lock --update-input my-tool`
- [ ] Build to verify: `nix build .#my-tool` or `nh darwin switch --no-nom .`

______________________________________________________________________

## Pattern 3: External binary / rolling-release app (sources.json)

For pre-built binaries, .dmg apps, AppImages, or apps with their own update APIs (not Git source repos). These are NOT flake inputs.

### Files to modify

#### Per-package `sources.json`

Create `overlays/my-app/sources.json` as a **bare entry**:

```json
{
  "hashes": {
    "aarch64-darwin": "sha256-...",
    "x86_64-darwin": "sha256-...",
    "x86_64-linux": "sha256-..."
  },
  "urls": {
    "aarch64-darwin": "https://example.com/my-app-arm64.dmg",
    "x86_64-darwin": "https://example.com/my-app-x64.dmg",
    "x86_64-linux": "https://example.com/my-app-x64.AppImage"
  },
  "version": "1.0.0"
}
```

Compute hashes with: `nix-prefetch-url --type sha256 <url>` then convert with `nix hash convert --hash-algo sha256 --to sri <hash>`

#### `overlays/my-app/default.nix` - Add overlay fragment

Choose based on delivery format:

##### Override existing nixpkgs package (simplest)

Uses the `mkSourceOverride` helper:

```nix
{ final, sources, ... }:
{
  my-app = final.mkSourceOverride "my-app" prev.my-app;
}
```

For nested packages:

```nix
{ final, prev, sources, ... }:
{
  jetbrains = prev.jetbrains // {
    my-ide = final.mkSourceOverride "my-ide" prev.jetbrains.my-ide;
  };
}
```

##### macOS .dmg app

```nix
{ final, prev, sources, ... }:
{
  my-app = final.mkDmgApp {
    pname = "my-app";
    info = sources.my-app;
    meta = with prev.lib; {
      description = "Description";
      homepage = "https://example.com";
      license = licenses.unfree;
      platforms = platforms.darwin;
      sourceProvenance = with sourceTypes; [ binaryNativeCode ];
      mainProgram = "my-app";
    };
  };
}
```

##### Pre-built binary

```nix
{ prev, sources, system, ... }:
let
  info = sources.my-tool;
in
{
  my-tool = prev.stdenvNoCC.mkDerivation {
    pname = "my-tool";
    inherit (info) version;
    src = prev.fetchurl {
      url = info.urls.${system};
      hash = info.hashes.${system};
    };
    dontUnpack = true;
    installPhase = ''
      runHook preInstall
      mkdir -p $out/bin
      cp $src $out/bin/my-tool
      chmod +x $out/bin/my-tool
      runHook postInstall
    '';
    meta = with prev.lib; {
      description = "Description";
      homepage = "https://example.com";
      license = licenses.unfree;
      platforms = [ "aarch64-darwin" "x86_64-linux" ];
      sourceProvenance = with sourceTypes; [ binaryNativeCode ];
      mainProgram = "my-tool";
    };
  };
}
```

##### Single file from GitHub (like homebrew-zsh-completion)

For source-only files, place them under `packages/` (same update/discovery flow):

```nix
# In packages/ or via outputs.lib:
let
  source = outputs.lib.sourceHashEntry "my-config" "sha256";
in
prev.stdenvNoCC.mkDerivation {
  name = "my-config";
  src = builtins.fetchurl {
    inherit (source) url;
    sha256 = source.hash;
  };
  dontUnpack = true;
  installPhase = ''
    mkdir -p $out
    cp $src $out/my-config
  '';
};
```

#### Per-package `updater.py` - Add Updater class

Create `overlays/my-app/updater.py`:

##### `DownloadHashUpdater` - must download to compute hash

```python
"""Updater for MyApp."""

import aiohttp

from lib.nix.models.sources import SourceEntry, SourceHashes
from lib.update.net import fetch_json
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo


class MyAppUpdater(DownloadHashUpdater):
    """Update MyApp to latest version."""

    name = "my-app"

    PLATFORMS = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        data = await fetch_json(session, "https://api.example.com/latest")
        return VersionInfo(version=data["version"], metadata={})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return f"https://example.com/download/{info.version}/{self.PLATFORMS[platform]}"

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {p: self.get_download_url(p, info) for p in self.PLATFORMS}
        return self._build_result_with_urls(info, hashes, urls)
```

##### `ChecksumProvidedUpdater` - API provides checksums (no download needed)

```python
"""Updater for MyApp."""

import aiohttp

from lib.nix.models.sources import SourceEntry, SourceHashes
from lib.update.net import fetch_json
from lib.update.updaters.base import ChecksumProvidedUpdater, VersionInfo


class MyAppUpdater(ChecksumProvidedUpdater):
    """Update MyApp to latest version."""

    name = "my-app"

    PLATFORMS = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-linux": "linux-x64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        data = await fetch_json(session, "https://api.example.com/releases/latest")
        return VersionInfo(version=data["version"], metadata={"release": data})

    async def fetch_checksums(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> dict[str, str]:
        release = info.metadata["release"]
        return {
            platform: release["checksums"][api_key]
            for platform, api_key in self.PLATFORMS.items()
        }

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {p: f"https://example.com/{v}" for p, v in self.PLATFORMS.items()}
        return self._build_result_with_urls(info, hashes, urls)
```

##### `GitHubRawFileUpdater` - single file from a GitHub repo

```python
from lib.update.updaters.github_raw_file import github_raw_file_updater

github_raw_file_updater(
    "my-config-file",
    owner="owner",
    repo="repo",
    path="path/to/file",
)
```

##### `PlatformAPIUpdater` - per-platform API with version/checksum fields

```python
"""Updater for MyApp platform builds."""

from lib.update.updaters.base import VersionInfo
from lib.update.updaters.platform_api import PlatformAPIUpdater


class MyAppUpdater(PlatformAPIUpdater):
    """Updater for MyApp builds."""

    name = "my-app"
    PLATFORMS = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-linux": "linux-x64",
    }
    VERSION_KEY = "productVersion"
    CHECKSUM_KEY = "sha256hash"

    def _api_url(self, api_platform: str) -> str:
        return f"https://api.example.com/update/{api_platform}/latest"

    def _download_url(self, api_platform: str, info: VersionInfo) -> str:
        return f"https://example.com/{info.version}/{api_platform}"
```

### Checklist

- [ ] Create package directory: `overlays/my-app/` or `packages/my-app/`
- [ ] Add `sources.json` with version, urls, and hashes (bare entry format)
- [ ] Add `default.nix` overlay fragment
- [ ] Add `updater.py` with Updater class or factory call
- [ ] Verify: `nixcfg update --validate` and `nixcfg update my-app`
- [ ] Build to verify: `nix build .#my-app` or `nh darwin switch --no-nom .`

______________________________________________________________________

## Key helpers in overlays/default.nix

| Helper | Purpose | Needs sources.json? |
|---|---|---|
| `mkGoCliPackage` | Build Go CLI with completions | Yes (vendorHash) |
| `mkUv2nixPackage` | Build Python package via uv2nix | No |
| `mkDmgApp` | Install macOS .dmg app | Yes (full entry) |
| `mkSourceOverride` | Override nixpkgs pkg version+src | Yes (full entry) |

## Key utilities in outputs.lib

| Function | What it does |
|---|---|
| `outputs.lib.sourceHash "name" "hashType"` | Read hash from per-package sources.json by hashType |
| `outputs.lib.sourceHashEntry "name" "hashType"` | Read full hash entry (url + hash) from sources.json |
| `outputs.lib.sources` | Pre-parsed sources entries (aggregated from all per-package files) |
| `outputs.lib.flakeLock` | Pre-parsed flake.lock nodes |

## Helper functions in overlays/default.nix

| Function | What it does |
|---|---|
| `normalizeName s` | Replace `.` and `_` with `-` |
| `stripVersionPrefix s` | Remove `v` / `rust-v` prefixes |

## Updater factory functions (in `update/updaters/base.py`)

| Factory | Registers | Hash type |
|---|---|---|
| `go_vendor_updater(name, ...)` | `GoVendorHashUpdater` | vendorHash |
| `cargo_vendor_updater(name, ...)` | `CargoVendorHashUpdater` | cargoHash |
| `npm_deps_updater(name, ...)` | `NpmDepsHashUpdater` | npmDepsHash |
| `deno_deps_updater(name, ...)` | `DenoDepsHashUpdater` | denoDepsHash |
| `bun_node_modules_updater(name, ...)` | `BunNodeModulesHashUpdater` | nodeModulesHash |
| `github_raw_file_updater(name, ...)` | `GitHubRawFileUpdater` | sha256 |

## Overlay fragment convention

Each overlay fragment in `overlays/<name>/default.nix` receives these args and returns an attrset:

```nix
{ inputs, outputs, final, prev, system, slib, sources, ... }:
{
  # attrs to merge into the overlay
}
```
