{
  bun,
  bunVersion,
  cacert,
  src,
  stdenvNoCC,
  version,
  hash,
}:
assert bun.version == bunVersion;
stdenvNoCC.mkDerivation {
  pname = "openchamber-node-modules";
  inherit src version;

  nativeBuildInputs = [
    bun
    cacert
  ];

  strictDeps = true;
  dontPatchShebangs = true;
  dontFixup = true;

  buildPhase = ''
    runHook preBuild

    export HOME="$TMPDIR/openchamber-home"
    export BUN_INSTALL_CACHE_DIR="$TMPDIR/openchamber-bun-cache"
    export SSL_CERT_FILE="${cacert}/etc/ssl/certs/ca-bundle.crt"
    mkdir -p "$HOME" "$BUN_INSTALL_CACHE_DIR"

    # Scripts would download Electron, OpenCode and native release artifacts.
    # Those inputs are all supplied by package-local source derivations below.
    bun install \
      --cpu=arm64 \
      --os=darwin \
      --frozen-lockfile \
      --ignore-scripts \
      --no-progress

    # Never let the npm prebuilt become a runtime input. The wrapper and Darwin
    # addon are installed from audited package-local sources.
    rm -rf \
      node_modules/sherpa-onnx-node \
      node_modules/sherpa-onnx-darwin-arm64 \
      node_modules/.bun/sherpa-onnx-node@* \
      node_modules/.bun/sherpa-onnx-darwin-arm64@*

    # Bun can create package-local bin links nondeterministically inside its
    # private .bun store. The build only needs top-level node_modules links,
    # so remove nested .bin directories before hashing the output.
    if [ -d node_modules/.bun ]; then
      find node_modules/.bun -path '*/node_modules/.bin' -type d -prune -exec rm -rf {} +
    fi

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p "$out"
    cp -R node_modules "$out/node_modules"
    find packages -type d -name node_modules -exec cp -R --parents {} "$out" \;

    runHook postInstall
  '';

  outputHashAlgo = "sha256";
  outputHashMode = "recursive";
  outputHash = hash;

  meta.platforms = [ "aarch64-darwin" ];
}
