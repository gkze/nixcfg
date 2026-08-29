{
  autoconf,
  automake,
  cctools,
  fetchFromGitHub,
  fetchPnpmDeps ? null,
  lib,
  libiconv,
  makeWrapper,
  nixcfgElectron,
  nodejs_22,
  outputs,
  pkg-config,
  pnpmConfigHook,
  pnpm_10,
  python3,
  runCommand,
  selfSource,
  stdenv,
  ...
}:
let
  pname = "bb";
  appName = "bb";
  appBundleName = "${appName}.app";
  appId = "dev.bb.desktop";
  inherit (selfSource) electronVersion version;

  upstreamSrc = fetchFromGitHub {
    owner = "get-bb";
    repo = "bb";
    rev = selfSource.commit;
    hash = outputs.lib.sourceHash pname "srcHash";
  };
  src = runCommand "${pname}-${version}-pnpm10-source" { nativeBuildInputs = [ python3 ]; } ''
    cp -R ${upstreamSrc} "$out"
    chmod -R u+w "$out"
    ${lib.getExe python3} ${./normalize_pnpm_patch_hashes.py} \
      --source "$out"
  '';

  nodejs = nodejs_22;
  pnpm = pnpm_10.override { nodejs-slim = nodejs; };
  pnpmDeps =
    let
      args = {
        inherit
          pname
          pnpm
          src
          version
          ;
        fetcherVersion = 3;
        hash = outputs.lib.sourceHash pname "npmDepsHash";
      };
    in
    if fetchPnpmDeps != null then fetchPnpmDeps args else pnpm.fetchDeps args;

  electronBuild = nixcfgElectron.sourceBuildFor electronVersion;
  electronRuntime = electronBuild.runtime;
  electronRuntimeVersion = electronBuild.runtimeVersion;
  electronHeaders = electronBuild.headers;
  electronDist = electronBuild.dist;

  electronRuntimeVersionCheck =
    if electronRuntimeVersion == electronVersion then
      true
    else
      throw ''
        packages/bb/default.nix needs Electron ${electronVersion},
        but the selected runtime is ${electronRuntimeVersion}; add the exact runtime to nixcfgElectron
      '';
in
assert electronRuntimeVersionCheck;
stdenv.mkDerivation {
  inherit
    pname
    pnpmDeps
    src
    version
    ;

  patches = [
    ./patches/nix-managed-updates.patch
    ./patches/nix-managed-connect-credential-cache.patch
    ./patches/nix-managed-runtime-closure.patch
    ./patches/pnpm-10-hoisted-runtime-manifest.patch
  ];

  nativeBuildInputs = [
    autoconf
    automake
    cctools
    libiconv
    makeWrapper
    nodejs
    pkg-config
    pnpm
    pnpmConfigHook
    python3
  ];

  strictDeps = true;

  env = electronBuild.commonEnv // {
    CI = "1";
    CSC_IDENTITY_AUTO_DISCOVERY = "false";
    NODE_OPTIONS = "--max-old-space-size=6144";
    npm_config_build_from_source = "true";
    npm_config_manage_package_manager_versions = "false";
    npm_config_node_linker = "hoisted";
  };

  prePatch = ''
    ${lib.getExe python3} ${./validate_source_metadata.py} \
      --source . \
      --expected-version ${lib.escapeShellArg version} \
      --expected-electron-version ${lib.escapeShellArg electronVersion}
  '';

  postPatch = ''
    cp \
      ${./nix-managed-connect-credential-encryption.ts} \
      apps/desktop/src/nix-managed-connect-credential-encryption.ts
    cp \
      ${./nix-managed-connect-credential.test.ts} \
      apps/desktop/test/nix-managed-connect-credential.test.ts

    # The upstream hook downloads a better-sqlite3 prebuild after packaging.
    # Nix rebuilds that module from the pinned source closure instead.
    ${lib.getExe python3} - <<'PY'
    import json
    from pathlib import Path

    path = Path("apps/desktop/electron-builder.config.json")
    config = json.loads(path.read_text())
    del config["afterPack"]
    path.write_text(json.dumps(config, indent=2) + "\n")
    PY
  '';

  buildPhase = ''
    runHook preBuild

    export TURBO_CACHE_DIR="$TMPDIR/turbo-cache"
    export TURBO_TELEMETRY_DISABLED=1
    mkdir -p "$TURBO_CACHE_DIR"

    pnpm exec turbo run build --filter=@bb/desktop --output-logs=new-only

    # pnpmConfigHook intentionally does not execute dependency lifecycle
    # scripts. Rebuild the two native modules loaded by the packaged Electron
    # runtime against its exact ABI before electron-builder copies them.
    pushd apps/desktop
    pnpm exec electron-rebuild \
      -f \
      -v ${electronVersion} \
      --only=better-sqlite3,node-pty

    ${electronBuild.copyDist}

    pnpm exec electron-builder \
      --mac \
      --arm64 \
      --dir \
      --publish never \
      --config electron-builder.config.json \
      -c.mac.identity=null \
      -c.npmRebuild=false \
      ${electronBuild.electronBuilderConfigFlags}

    # Upstream's afterPack hook fetches a better-sqlite3 prebuild. The module
    # is already source-built above; invoke its offline mode to retain only the
    # node-pty helper-path and executable-mode repairs.
    node scripts/prepare-native-modules.cjs "release/mac-arm64/${appBundleName}"
    popd

    runHook postBuild
  '';

  doCheck = true;
  checkPhase = ''
    runHook preCheck

    pushd apps/desktop
    pnpm exec vitest run \
      --config vitest.config.ts \
      test/nix-managed-connect-credential.test.ts
    popd

    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall

    appBundle="apps/desktop/release/mac-arm64/${appBundleName}"
    if [ ! -d "$appBundle" ]; then
      echo "failed to locate packaged ${appBundleName} at $appBundle" >&2
      exit 1
    fi

    mkdir -p "$out/Applications" "$out/bin"
    cp -R "$appBundle" "$out/Applications/${appBundleName}"
    makeWrapper \
      "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" \
      "$out/bin/${pname}"

    runHook postInstall
  '';

  postFixup = ''
    /usr/bin/xattr -cr "$out/Applications/${appBundleName}"
    /usr/bin/codesign --force --deep --sign - "$out/Applications/${appBundleName}"
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    for path in \
      "$out/Applications/${appBundleName}" \
      "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" \
      "$out/Applications/${appBundleName}/Contents/Resources/app.asar" \
      "$out/Applications/${appBundleName}/Contents/Resources/app.asar.unpacked" \
      "$out/bin/${pname}"
    do
      if [ ! -e "$path" ]; then
        echo "missing required runtime path: $path" >&2
        exit 1
      fi
    done

    /usr/bin/codesign --verify --deep --strict "$out/Applications/${appBundleName}"

    ELECTRON_RUN_AS_NODE=1 "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" <<'NODE'
    const nodeModules =
      process.env.out
      + "/Applications/${appBundleName}/Contents/Resources/app.asar/node_modules";

    for (const packageName of ["@parcel/watcher", "better-sqlite3", "node-pty"]) {
      require(nodeModules + "/" + packageName);
      console.log(packageName + " ok");
    }

    // Load the logger transport through its real transitive chain. A mere
    // directory-presence check missed once -> wrappy in the packaged ASAR.
    require(nodeModules + "/pino-pretty");
    console.log("pino-pretty runtime closure ok");
    NODE

    ${lib.getExe python3} - "$out/Applications/${appBundleName}/Contents/Info.plist" <<'PY'
    import plistlib
    import sys

    with open(sys.argv[1], "rb") as plist_file:
        info = plistlib.load(plist_file)

    expected = {
        "CFBundleDisplayName": "${appName}",
        "CFBundleExecutable": "${appName}",
        "CFBundleIdentifier": "${appId}",
        "CFBundleShortVersionString": "${version}",
        "CFBundleVersion": "${version}",
    }
    for key, expected_value in expected.items():
        actual_value = info.get(key)
        if actual_value != expected_value:
            raise SystemExit(f"{key} expected {expected_value!r}, got {actual_value!r}")
    PY

    runHook postInstallCheck
  '';

  passthru = {
    inherit
      electronDist
      electronHeaders
      electronRuntime
      electronRuntimeVersion
      electronVersion
      pnpmDeps
      ;
    macApp = {
      bundleName = appBundleName;
      bundleRelPath = "Applications/${appBundleName}";
      installMode = "copy";
    };
  };

  meta = with lib; {
    description = "Agentic IDE that can control, customize, and automate itself";
    homepage = "https://getbb.app/";
    license = licenses.mit;
    mainProgram = pname;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ fromSource ];
  };
}
