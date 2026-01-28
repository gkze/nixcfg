______________________________________________________________________

## name: nix-add-package description: Add a new package to this nixcfg flake using the correct pattern based on source type (flake input, non-flake source, or external binary) license: MIT compatibility: opencode metadata: audience: maintainers workflow: nix

## What I do

Guide you through adding a new package to this nixcfg repository. There are three main patterns, each with sub-variants. I help you choose the right one and walk you through every file that needs changes.

## When to use me

Use this when you want to add a new package, tool, or application to the Nix configuration. Ask clarifying questions if unsure which pattern to apply.

## Decision tree

1. **Is the source a Nix flake that exports packages or overlays?** -> Pattern 1
1. **Is the source a Git repo (flake=false) you build from source?** -> Pattern 2
1. **Is the source a pre-built binary, .dmg, AppImage, or rolling-release app?** -> Pattern 3

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

No changes to `overlays.nix` needed.

#### Option B: Direct package assignment in overlay

In `overlays.nix`, inside `default = final: prev: { ... }`:

```nix
my-package = inputs.my-package.packages.${system}.default;
```

### Checklist

- [ ] Add flake input with `inputs.nixpkgs.follows = "nixpkgs"`
- [ ] Either add overlay to `withOverlays` OR add assignment in `overlays.nix`
- [ ] Run `nix flake lock --update-input my-package` to populate lock
- [ ] No `sources.json` or `update.py` changes needed

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

#### `overlays.nix` - Add derivation

Choose the appropriate helper based on the build system:

##### Go CLI (with shell completions)

```nix
my-tool = mkGoCliPackage {
  pname = "my-tool";
  input = inputs.my-tool;
  subPackages = [ "cmd/my-tool" ];  # or [ "." ]
  cmdName = "my-tool";  # binary name (defaults to pname)
  meta = with prev.lib; {
    description = "Description";
    homepage = "https://github.com/owner/repo";
    license = licenses.mit;
  };
};
```

Requires `vendorHash` in sources.json (see below).

##### Python (uv2nix)

```nix
my-tool = mkUv2nixPackage {
  name = "my-tool";
  src = inputs.my-tool;          # or "${inputs.my-tool}/subdir"
  mainProgram = "my-tool";
  # Optional:
  # pythonVersion = prev.python314;
  # packageName = "pypi-name";   # if different from name
  # venvName = "my-tool";
};
```

No sources.json entry needed (uv2nix handles deps).

##### Cargo/Rust override

```nix
my-tool = prev.my-tool.overrideAttrs (old: {
  version = getFlakeVersion "my-tool";
  src = inputs.my-tool;
  cargoDeps = prev.rustPlatform.fetchCargoVendor {
    src = inputs.my-tool;
    hash = outputs.lib.sourceHash "my-tool" "cargoHash";
  };
});
```

Requires `cargoHash` in sources.json.

##### Go override (existing nixpkgs package)

```nix
my-tool = prev.my-tool.overrideAttrs {
  version = getFlakeVersion "my-tool";
  src = inputs.my-tool;
  vendorHash = outputs.lib.sourceHash "my-tool" "vendorHash";
};
```

Requires `vendorHash` in sources.json.

##### npm/Node override

```nix
my-tool =
  let
    version = getFlakeVersion "my-tool";
    npmDepsHash = outputs.lib.sourceHash "my-tool" "npmDepsHash";
    npmDeps = prev.fetchNpmDeps {
      src = inputs.my-tool;
      hash = npmDepsHash;
    };
  in
  prev.my-tool.overrideAttrs (old: {
    inherit version npmDepsHash npmDeps;
    src = inputs.my-tool;
  });
```

Requires `npmDepsHash` in sources.json.

##### Deno package (like linear-cli)

```nix
my-tool =
  let
    version = "1.0.0";
    # FOD: fetch Deno dependencies (needs network)
    denoDeps = prev.stdenvNoCC.mkDerivation {
      pname = "my-tool-deps";
      inherit version;
      src = inputs.my-tool;
      nativeBuildInputs = [ prev.deno prev.cacert ];
      outputHashAlgo = "sha256";
      outputHashMode = "recursive";
      outputHash = outputs.lib.sourceHash "my-tool" "denoDepsHash";
      buildPhase = ''
        export DENO_DIR=$TMPDIR/deno-cache
        export SSL_CERT_FILE=${prev.cacert}/etc/ssl/certs/ca-bundle.crt
        export HOME=$TMPDIR
        deno cache src/main.ts
      '';
      installPhase = ''
        mkdir -p $out
        cp -r $TMPDIR/deno-cache $out/deno-cache
      '';
    };
  in
  prev.stdenvNoCC.mkDerivation {
    pname = "my-tool";
    inherit version;
    src = inputs.my-tool;
    nativeBuildInputs = [ prev.deno prev.installShellFiles ];
    buildPhase = ''
      export DENO_DIR=$(mktemp -d)
      cp -r ${denoDeps}/deno-cache/* $DENO_DIR/
      chmod -R u+w $DENO_DIR
      deno compile -A --output my-tool src/main.ts
    '';
    installPhase = ''
      mkdir -p $out/bin
      cp my-tool $out/bin/
    '';
  };
```

Requires `denoDepsHash` in sources.json.

##### Vim plugin

```nix
# Inside vimPlugins = prev.vimPlugins.extend (_: vprev: { ... }):

# New plugin from source:
my-plugin = prev.vimUtils.buildVimPlugin {
  pname = "my-plugin";
  version = inputs.my-plugin.rev;
  src = inputs.my-plugin;
};

# Override existing plugin's source:
my-plugin = vprev.my-plugin.overrideAttrs {
  src = inputs.my-plugin;
};
```

No sources.json entry needed.

#### `sources.json` - Add hash entry (if needed)

Only needed for Go (`vendorHash`), Cargo (`cargoHash`), npm (`npmDepsHash`), or Deno (`denoDepsHash`) builds. The entry stores the input name and computed hashes:

```json
{
  "my-tool": {
    "hashes": [
      {
        "drvType": "buildGoModule",
        "hash": "sha256-PLACEHOLDER",
        "hashType": "vendorHash"
      }
    ],
    "input": "my-tool"
  }
}
```

To compute the actual hash, temporarily use `lib.fakeHash` and build to get the expected hash from the error output, or run `./update.py my-tool`.

#### `update.py` - Register updater (if sources.json entry exists)

Use the appropriate factory function at the bottom of the updater registrations section (around line 1834):

```python
# Go:
go_vendor_updater("my-tool", subpackages=["cmd/my-tool"])
# With proxy vendor:
go_vendor_updater("my-tool", subpackages=["cmd/my-tool"], proxy_vendor=True)

# Cargo:
cargo_vendor_updater("my-tool")
# With subdirectory:
cargo_vendor_updater("my-tool", subdir="my-subdir")

# npm:
npm_deps_updater("my-tool")

# Deno:
deno_deps_updater("my-tool")
```

### Checklist

- [ ] Add flake input with `flake = false;` (and version tag if applicable)
- [ ] Add derivation in `overlays.nix` using appropriate helper
- [ ] If Go/Cargo/npm/Deno: add hash entry in `sources.json`
- [ ] If Go/Cargo/npm/Deno: register updater in `update.py`
- [ ] Run `nix flake lock --update-input my-tool`
- [ ] Build to verify: `nix build .#my-tool` or `nh darwin switch --no-nom .`

______________________________________________________________________

## Pattern 3: External binary / rolling-release app (sources.json)

For pre-built binaries, .dmg apps, AppImages, or apps with their own update APIs (not Git source repos). These are NOT flake inputs.

### Files to modify

#### `sources.json` - Add full entry

```json
{
  "my-app": {
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
}
```

Compute hashes with: `nix-prefetch-url --type sha256 <url>` then convert with `nix hash convert --hash-algo sha256 --to sri <hash>`

#### `overlays.nix` - Add derivation

Choose based on delivery format:

##### Override existing nixpkgs package (simplest)

Uses the `mkSourceOverride` helper:

```nix
my-app = mkSourceOverride "my-app" prev.my-app;

# For nested packages:
jetbrains = prev.jetbrains // {
  my-ide = mkSourceOverride "my-ide" prev.jetbrains.my-ide;
};
```

##### macOS .dmg app

```nix
my-app = mkDmgApp {
  pname = "my-app";
  info = sources.my-app;
  # appName = "my-app";  # if capitalized name differs from pname
  meta = with prev.lib; {
    description = "Description";
    homepage = "https://example.com";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "my-app";
  };
};
```

##### Pre-built binary

```nix
my-tool =
  let
    info = sources.my-tool;
  in
  prev.stdenvNoCC.mkDerivation {
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
```

##### Single file from GitHub (like homebrew-zsh-completion)

```nix
my-config =
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

#### `update.py` - Add Updater class

Choose the base class that fits:

##### `DownloadHashUpdater` - must download to compute hash

```python
class MyAppUpdater(DownloadHashUpdater):
    """Update MyApp to latest version."""
    name = "my-app"

    PLATFORMS = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        # Fetch version from API, appcast, install script, etc.
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
github_raw_file_updater(
    "my-config-file",
    owner="owner",
    repo="repo",
    path="path/to/file",
)
```

### Checklist

- [ ] Add entry in `sources.json` with version, urls, and hashes
- [ ] Add derivation in `overlays.nix`
- [ ] Add Updater class or factory call in `update.py`
- [ ] Verify: `./update.py --validate` and `./update.py my-app`
- [ ] Build to verify: `nix build .#my-app` or `nh darwin switch --no-nom .`

______________________________________________________________________

## Key helpers in overlays.nix

| Helper | Purpose | Needs sources.json? |
|---|---|---|
| `mkGoCliPackage` | Build Go CLI with completions | Yes (vendorHash) |
| `mkUv2nixPackage` | Build Python package via uv2nix | No |
| `mkDmgApp` | Install macOS .dmg app | Yes (full entry) |
| `mkSourceOverride` | Override nixpkgs pkg version+src | Yes (full entry) |
| `getFlakeVersion` | Extract version from flake lock ref | No |

## Key utilities in outputs.lib

| Function | What it does |
|---|---|
| `outputs.lib.sourceHash "name" "hashType"` | Read hash from sources.json by hashType |
| `outputs.lib.sourceHashEntry "name" "hashType"` | Read full hash entry (url + hash) from sources.json |
| `outputs.lib.sources` | Pre-parsed sources.json entries |
| `outputs.lib.flakeLock` | Pre-parsed flake.lock nodes |

## Helper functions in overlays.nix

| Function | What it does |
|---|---|
| `normalizeName s` | Replace `.` and `_` with `-` |
| `stripVersionPrefix s` | Remove `v` / `rust-v` prefixes |

## Updater factory functions in update.py

| Factory | Registers | Hash type |
|---|---|---|
| `go_vendor_updater(name, ...)` | `GoVendorHashUpdater` | vendorHash |
| `cargo_vendor_updater(name, ...)` | `CargoVendorHashUpdater` | cargoHash |
| `npm_deps_updater(name, ...)` | `NpmDepsHashUpdater` | npmDepsHash |
| `deno_deps_updater(name, ...)` | `DenoDepsHashUpdater` | denoDepsHash |
| `github_raw_file_updater(name, ...)` | `GitHubRawFileUpdater` | sha256 |
