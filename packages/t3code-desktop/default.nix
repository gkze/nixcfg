{
  bun,
  cacert,
  fetchPnpmDeps ? null,
  inputs,
  lib,
  makeWrapper,
  nixcfgElectron,
  nodejs,
  outputs,
  pkgs,
  pnpm_10,
  pnpmConfigHook,
  python3,
  stdenv,
  ...
}:
let
  pname = "t3code-desktop";
  appName = "T3 Code (Alpha)";
  appBundleName = "${appName}.app";
  appId = "com.t3tools.t3code";
  appProtocolScheme = "t3";
  electronBuilderVersion = "26.8.1";
  updateRuntimeLocksTemplate = ./update_runtime_locks.py;

  shared = import ../t3code/_shared.nix {
    inherit
      bun
      cacert
      fetchPnpmDeps
      inputs
      lib
      nodejs
      outputs
      pnpm_10
      pnpmConfigHook
      stdenv
      ;
    sourceHashPackageName = "t3code-workspace";
  };
  inherit (shared)
    src
    version
    workspaceBuild
    ;
  inherit (stdenv.hostPlatform) system;

  serverPackageJson = builtins.fromJSON (builtins.readFile "${src}/apps/server/package.json");
  desktopPackageJson = builtins.fromJSON (builtins.readFile "${src}/apps/desktop/package.json");
  appVersion = serverPackageJson.version;
  electronVersion = desktopPackageJson.dependencies.electron;
  electronBuild = nixcfgElectron.sourceBuildFor electronVersion;
  electronRuntime = electronBuild.runtime;
  electronRuntimeVersion = electronBuild.runtimeVersion;
  electronHeaders = electronBuild.headers;
  electronDist = electronBuild.dist;
  versionSyncCheck =
    if serverPackageJson.version == desktopPackageJson.version then
      true
    else
      throw ''
        packages/t3code-desktop/default.nix expected matching upstream versions,
        got server ${serverPackageJson.version} and desktop ${desktopPackageJson.version}
      '';
  t3codeCommitHash = outputs.lib.flakeLock.t3code.locked.rev or "";
  pythonForRuntimeManifest = python3.withPackages (ps: [ ps.pyyaml ]);
  updateRuntimeLocks = pkgs.writeTextFile {
    name = "update-t3code-runtime-locks";
    destination = "/bin/update-t3code-runtime-locks";
    executable = true;
    text =
      "#!${lib.getExe pythonForRuntimeManifest}\n"
      +
        builtins.replaceStrings
          [
            "@UPSTREAM_SRC@"
            "@BUN@"
            "@ELECTRON_BUILDER_VERSION@"
            "@T3CODE_COMMIT_HASH@"
          ]
          [
            (toString src)
            (lib.getExe bun)
            electronBuilderVersion
            t3codeCommitHash
          ]
          (builtins.readFile updateRuntimeLocksTemplate);
  };

  node_modules = stdenv.mkDerivation {
    pname = "${pname}-node_modules";
    inherit version src;

    nativeBuildInputs = [
      bun
      cacert
      pythonForRuntimeManifest
    ];

    dontPatchShebangs = true;
    dontFixup = true;

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR/home"
      mkdir -p "$HOME"
      export SSL_CERT_FILE="${cacert}/etc/ssl/certs/ca-bundle.crt"
      export NODE_EXTRA_CA_CERTS="$SSL_CERT_FILE"
      export BUN_INSTALL_CACHE_DIR="$TMPDIR/.bun-cache"

      ${lib.getExe pythonForRuntimeManifest} ${./render_runtime_package_json.py} \
        ${src} \
        --electron-builder-version ${lib.escapeShellArg electronBuilderVersion} \
        --commit-hash ${lib.escapeShellArg t3codeCommitHash} \
        --output package.json
      cp ${./bun.lock} bun.lock

      bun install \
        --frozen-lockfile \
        --ignore-scripts \
        --no-progress

      ${lib.getExe pythonForRuntimeManifest} - <<'PY'
      import json
      from pathlib import Path

      package_json = Path("node_modules/@pierre/diffs/package.json")
      payload = json.loads(package_json.read_text(encoding="utf-8"))
      exports = payload.get("exports")
      if not isinstance(exports, dict):
          raise TypeError("@pierre/diffs package.json exports must be an object")
      exports["./utils/*"] = {
          "types": "./dist/utils/*.d.ts",
          "import": "./dist/utils/*.js",
      }
      package_json.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
      PY

      # Bun can create package-local bin links nondeterministically inside its
      # private .bun store. The desktop build only uses top-level node_modules
      # links, so remove nested .bin directories before hashing the output.
      if [ -d node_modules/.bun ]; then
        find node_modules/.bun -path '*/node_modules/.bin' -type d -prune -exec rm -rf {} +
      fi

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      mkdir -p "$out"
      cp package.json "$out/package.json"
      cp bun.lock "$out/bun.lock"
      cp -R node_modules "$out/node_modules"
      ${lib.getExe python3} - <<'PY'
      import json
      import os
      import pathlib
      import shutil

      root = pathlib.Path(".")
      out = pathlib.Path(os.environ["out"])
      workspaces = json.loads((root / "package.json").read_text(encoding="utf-8")).get("workspaces", {})
      for rel_dir in workspaces.get("packages", []):
          src = root / rel_dir
          if src.is_dir():
              dst = out / rel_dir
              dst.parent.mkdir(parents=True, exist_ok=True)
              shutil.copytree(src, dst, ignore=shutil.ignore_patterns("node_modules", ".turbo"))
      PY

      runHook postInstall
    '';

    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = outputs.lib.sourceHashForPlatform pname "nodeModulesHash" system;
  };
in
assert versionSyncCheck;
stdenv.mkDerivation {
  inherit
    pname
    version
    src
    node_modules
    ;

  nativeBuildInputs = [
    bun
    makeWrapper
    nodejs
    python3
  ];

  strictDeps = true;

  env = electronBuild.commonEnv // {
    CI = "1";
    CSC_IDENTITY_AUTO_DISCOVERY = "false";
    NODE_OPTIONS = "--max-old-space-size=6144";
  };

  dontUnpack = true;

  buildPhase = ''
    runHook preBuild

    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    export BUN_INSTALL_CACHE_DIR="$TMPDIR/.bun-cache"
    export NODE_COMPILE_CACHE="$TMPDIR/node-compile-cache"
    export NODE_DISABLE_COMPILE_CACHE=1
    export SSL_CERT_FILE="${cacert}/etc/ssl/certs/ca-bundle.crt"
    export NODE_EXTRA_CA_CERTS="$SSL_CERT_FILE"

    appBuildRoot="$PWD/app-build-root"
    mkdir -p "$appBuildRoot"
    cd "$appBuildRoot"

    mkdir -p apps/desktop apps/server
    cp -R ${workspaceBuild}/apps/desktop/dist-electron apps/desktop/dist-electron
    cp -R ${workspaceBuild}/apps/desktop/resources apps/desktop/resources
    cp -R ${workspaceBuild}/apps/server/dist apps/server/dist
    cp -R ${node_modules}/node_modules ./node_modules
    if [ -d ${node_modules}/packages ]; then
      cp -R ${node_modules}/packages ./packages
    fi
    cp ${node_modules}/package.json ./package.json
    chmod -R u+w apps node_modules package.json
    if [ -d packages ]; then
      chmod -R u+w packages
    fi

    patchShebangs node_modules

    ${electronBuild.copyDist}

    ./node_modules/.bin/electron-builder \
      --mac dir \
      --publish never \
      -c.appId=${lib.escapeShellArg appId} \
      -c.productName=${lib.escapeShellArg appName} \
      -c.directories.buildResources=apps/desktop/resources \
      -c.mac.icon=icon.icns \
      -c.mac.category=public.app-category.developer-tools \
      -c.mac.identity=null \
      -c.mac.hardenedRuntime=false \
      -c.mac.notarize=false \
      ${electronBuild.electronBuilderConfigFlags} \
      -c.npmRebuild=false

    appResources="dist/mac-arm64/${appBundleName}/Contents/Resources"
    appAsar="$appResources/app.asar"
    appInfoPlist="dist/mac-arm64/${appBundleName}/Contents/Info.plist"
    asarScratch="$TMPDIR/t3code-app-asar"
    rm -rf "$asarScratch"
    mkdir -p "$asarScratch"

    T3CODE_APP_ASAR="$appAsar" \
    T3CODE_ASAR_SCRATCH="$asarScratch" \
      ${lib.getExe nodejs} <<'NODE'
    const fs = require("fs");
    const path = require("path");

    const asarRoots = fs
      .readdirSync("node_modules/.bun")
      .filter((entry) => entry.startsWith("@electron+asar@"))
      .sort();
    if (asarRoots.length !== 1) {
      throw new Error("expected exactly one @electron/asar package, got " + asarRoots.length);
    }

    const asar = require(path.join(
      process.cwd(),
      "node_modules/.bun",
      asarRoots[0],
      "node_modules/@electron/asar",
    ));
    const appAsar = process.env.T3CODE_APP_ASAR;
    const scratch = process.env.T3CODE_ASAR_SCRATCH;
    const extractDir = path.join(scratch, "extract");
    const nextAsar = path.join(scratch, "app.asar");
    const nextUnpacked = nextAsar + ".unpacked";
    const appUnpacked = appAsar + ".unpacked";
    const filenames = [];

    function compareNames(left, right) {
      if (left.name < right.name) return -1;
      if (left.name > right.name) return 1;
      return 0;
    }

    function collectFiles(relativeDirectory) {
      const absoluteDirectory = path.join(extractDir, relativeDirectory);
      for (const entry of fs.readdirSync(absoluteDirectory, { withFileTypes: true }).sort(compareNames)) {
        const relativePath = path.join(relativeDirectory, entry.name);
        const absolutePath = path.join(extractDir, relativePath);
        const stat = fs.lstatSync(absolutePath);
        if (stat.isDirectory()) {
          collectFiles(relativePath);
        } else if (stat.isFile() || stat.isSymbolicLink()) {
          filenames.push(absolutePath);
        }
      }
    }

    (async () => {
      fs.rmSync(extractDir, { recursive: true, force: true });
      fs.mkdirSync(extractDir, { recursive: true });
      asar.extractAll(appAsar, extractDir);
      collectFiles("");
      await asar.createPackageFromFiles(
        extractDir,
        nextAsar,
        filenames,
        {},
        { unpack: "*.node" },
      );
      fs.rmSync(appUnpacked, { recursive: true, force: true });
      if (fs.existsSync(nextUnpacked)) {
        fs.renameSync(nextUnpacked, appUnpacked);
      }
      fs.renameSync(nextAsar, appAsar);
    })().catch((error) => {
      console.error(error);
      process.exit(1);
    });
    NODE

    ${lib.getExe python3} ${../../lib/asar_integrity.py} \
      set-info-plist-hash "$appInfoPlist" "$appAsar"

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    appBundle="dist/mac-arm64/${appBundleName}"
    if [ ! -d "$appBundle" ]; then
      echo "failed to locate packaged ${appBundleName} in dist/mac-arm64" >&2
      exit 1
    fi

    mkdir -p "$out/Applications" "$out/bin"
    cp -R "$appBundle" "$out/Applications/${appBundleName}"

    ${lib.getExe python3} ${./patch_info_plist.py} \
      "$out/Applications/${appBundleName}/Contents/Info.plist" \
      --app-name ${lib.escapeShellArg appName} \
      --bundle-id ${lib.escapeShellArg appId} \
      --version ${lib.escapeShellArg appVersion} \
      --icon-file icon.icns \
      --url-scheme ${lib.escapeShellArg appProtocolScheme}

    makeWrapper \
      "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" \
      "$out/bin/${pname}"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    for path in \
      "$out/Applications/${appBundleName}" \
      "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" \
      "$out/bin/${pname}"
    do
      if [ ! -e "$path" ]; then
        echo "missing required runtime path: $path" >&2
        exit 1
      fi
    done

    if [ -L "$out/bin/${pname}" ]; then
      echo "expected $out/bin/${pname} to be a launcher script, not a symlink" >&2
      exit 1
    fi

    ${lib.getExe python3} ${../../lib/asar_integrity.py} \
      check-info-plist-hash \
      "$out/Applications/${appBundleName}/Contents/Info.plist" \
      "$out/Applications/${appBundleName}/Contents/Resources/app.asar"

    if [ ! -d "$out/Applications/${appBundleName}/Contents/Resources/app.asar.unpacked" ]; then
      echo "missing native module unpack directory for ${appBundleName} app.asar" >&2
      exit 1
    fi

    ELECTRON_RUN_AS_NODE=1 "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" <<'NODE'
    require(
      process.env.out
        + "/Applications/${appBundleName}/Contents/Resources/app.asar/node_modules/node-pty",
    );
    console.log("node-pty ok");
    NODE

    runHook postInstallCheck
  '';

  passthru = {
    inherit
      electronDist
      electronHeaders
      electronRuntime
      electronRuntimeVersion
      electronVersion
      updateRuntimeLocks
      ;

    macApp = {
      bundleName = appBundleName;
      bundleRelPath = "Applications/${appBundleName}";
      installMode = "copy";
    };
  };

  meta = with lib; {
    description = "T3 Code desktop app";
    homepage = "https://github.com/pingdotgg/t3code";
    license = licenses.mit;
    mainProgram = pname;
    platforms = [ "aarch64-darwin" ];
  };
}
