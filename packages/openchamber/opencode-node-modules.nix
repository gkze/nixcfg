{
  bun,
  bunVersion,
  lib,
  src,
  stdenvNoCC,
  version,
  hash,
}:
assert bun.version == bunVersion;
let
  platform = stdenvNoCC.hostPlatform;
  bunCpu = if platform.isAarch64 then "arm64" else "x64";
  bunOs = if platform.isLinux then "linux" else "darwin";
in
stdenvNoCC.mkDerivation {
  pname = "openchamber-opencode-node-modules";
  inherit src version;

  nativeBuildInputs = [ bun ];
  strictDeps = true;
  dontPatchShebangs = true;
  dontFixup = true;

  buildPhase = ''
    runHook preBuild

    export HOME="$TMPDIR/opencode-home"
    export BUN_INSTALL_CACHE_DIR="$TMPDIR/opencode-bun-cache"
    mkdir -p "$HOME" "$BUN_INSTALL_CACHE_DIR"
    bun install \
      --cpu=${lib.escapeShellArg bunCpu} \
      --os=${lib.escapeShellArg bunOs} \
      --filter '!./' \
      --filter './packages/opencode' \
      --filter './packages/desktop' \
      --filter './packages/app' \
      --frozen-lockfile \
      --ignore-scripts \
      --no-progress
    bun --bun nix/scripts/canonicalize-node-modules.ts
    bun --bun nix/scripts/normalize-bun-binaries.ts

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p "$out"
    find . -type d -name node_modules -exec cp -R --parents {} "$out" \;

    runHook postInstall
  '';

  outputHashAlgo = "sha256";
  outputHashMode = "recursive";
  outputHash = hash;

  meta.platforms = [ "aarch64-darwin" ];
}
