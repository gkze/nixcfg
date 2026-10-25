{
  bun,
  cacert,
  fetchPnpmDeps ? null,
  inputs,
  lib,
  makeWrapper,
  mkMacAppPassthru,
  nixcfgElectron,
  nodejs,
  outputs,
  pkgs,
  pnpm_12,
  pnpmConfigHook,
  python3,
  selfSource,
  stdenv,
  t3codeWorkspaceSource ? null,
  ...
}:
let
  pname = "t3code-desktop";
  appId = "com.t3tools.t3code";
  # Upstream registers both schemes (scripts/build-desktop-artifact.ts): deep
  # links and the OAuth handoff arrive as ``t3code://`` in packaged builds and
  # ``t3code-dev://`` in development.
  appProtocolSchemes = [
    "t3code"
    "t3code-dev"
  ];
  # 26.8.1 launched package-manager collectors through a shell on every
  # platform, which can leave electron-builder waiting forever on Darwin.
  # 26.15.x executes them directly off Windows and uses a dedicated safe
  # PowerShell path for Windows .cmd shims.
  electronBuilderVersion = selfSource.pins.electronBuilderVersion;
  updateRuntimeLocksTemplate = ./update_runtime_locks.py;

  shared = import ../t3code/_shared.nix {
    inherit
      cacert
      fetchPnpmDeps
      inputs
      lib
      nodejs
      outputs
      pnpm_12
      pnpmConfigHook
      stdenv
      ;
    inherit t3codeWorkspaceSource;
    sourceHashPackageName = "t3code-workspace";
  };
  inherit (shared)
    channel
    src
    version
    workspaceBuild
    ;
  inherit (stdenv.hostPlatform) system;

  serverPackageJson = builtins.fromJSON (builtins.readFile "${src}/apps/server/package.json");
  desktopPackageJson = builtins.fromJSON (builtins.readFile "${src}/apps/desktop/package.json");
  # The desktop bundle version is the channel version, not the workspace
  # package version: the app reads its own version at runtime to decide which
  # update channel it belongs to, so a nightly build must report the nightly
  # version recorded in the release tag.
  appVersion = version;
  # Mirrors resolveDesktopProductName in upstream's packaging script.
  appName =
    if channel == "nightly" then "T3 Code (Nightly)" else desktopPackageJson.productName or "T3 Code";
  appBundleName = "${appName}.app";
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
  # Upstream stages the macOS icon from the channel's artwork with sips and
  # iconutil (scripts/build-desktop-artifact.ts). Both need Xcode, so the
  # artwork is scaled with ImageMagick and assembled here instead. Without a
  # staged ``icon.icns`` the packager silently ships Electron's stock icon.
  channelIconPng =
    if channel == "nightly" then
      src + "/assets/nightly/nightly-macos-1024.png"
    else
      src + "/assets/prod/black-macos-1024.png";
  channelIcon =
    pkgs.runCommand "t3code-${channel}-icon"
      {
        nativeBuildInputs = [
          pkgs.imagemagick
          pythonForRuntimeManifest
        ];
      }
      ''
        ${lib.getExe pythonForRuntimeManifest} ${./make_icns.py} render ${channelIconPng} "$out"
      '';
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
    # electron-builder reads mac.icon relative to buildResources, and the
    # channel artwork has to be staged there for the app to ship a real icon.
    # Copy it after the permissions fixup: the staged resources are read-only.
    cp ${channelIcon} apps/desktop/resources/icon.icns

    patchShebangs node_modules

    # ffi-rs calls dlopen directly, outside Electron's virtual ASAR filesystem.
    # Upstream ships an ESM and a CJS bundle and the packaged app loads the CJS
    # one, so both entry points get the same rewrite.
    ${lib.getExe python3} ${./patch_fff_asar.py} \
      node_modules/@ff-labs/fff-node/dist/index.js \
      node_modules/@ff-labs/fff-node/dist/index.cjs

    ${electronBuild.copyDist}

    T3CODE_APP_ID=${lib.escapeShellArg appId} \
    T3CODE_APP_NAME=${lib.escapeShellArg appName} \
    T3CODE_APP_VERSION=${lib.escapeShellArg appVersion} \
    T3CODE_ELECTRON_DIST="$electronDistDir" \
    T3CODE_ELECTRON_VERSION=${lib.escapeShellArg electronRuntimeVersion} \
      ${lib.getExe nodejs} <<'NODE'
    "use strict";

    const { Arch, Platform, build } = require("electron-builder");

    function requireEnv(name) {
      const value = process.env[name];
      if (!value) {
        throw new Error(`Missing required environment variable: ''${name}`);
      }
      return value;
    }

    async function main() {
      const config = {
        appId: requireEnv("T3CODE_APP_ID"),
        productName: requireEnv("T3CODE_APP_NAME"),
        // The runtime install is pinned by bun.lock to the workspace package
        // version, so the channel version is stamped here instead. The desktop
        // app reads its own version to select an update channel.
        extraMetadata: {
          version: requireEnv("T3CODE_APP_VERSION"),
          buildVersion: requireEnv("T3CODE_APP_VERSION"),
        },
        directories: { buildResources: "apps/desktop/resources" },
        electronDist: requireEnv("T3CODE_ELECTRON_DIST"),
        electronVersion: requireEnv("T3CODE_ELECTRON_VERSION"),
        mac: {
          category: "public.app-category.developer-tools",
          hardenedRuntime: false,
          icon: "icon.icns",
          identity: null,
          notarize: false,
        },
        npmRebuild: false,
      };

      await build({
        config,
        publish: "never",
        targets: Platform.MAC.createTarget("dir", Arch.arm64),
      });
    }

    main().then(
      () => process.exit(0),
      (error) => {
        console.error(error);
        process.exit(1);
      },
    );
    NODE

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
        { unpack: "*.{node,dylib}" },
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
      ${lib.concatMapStringsSep " \\\n      " (
        scheme: "--url-scheme ${lib.escapeShellArg scheme}"
      ) appProtocolSchemes}

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

    appResources="$out/Applications/${appBundleName}/Contents/Resources"

    # The packager silently falls back to Electron's stock icon when
    # buildResources has no icon.icns, which is how this package shipped a
    # generic icon before.
    stockIcon="${electronDist}/Electron.app/Contents/Resources/electron.icns"
    if [ ! -f "$stockIcon" ]; then
      echo "missing Electron stock icon at $stockIcon" >&2
      exit 1
    fi
    ${lib.getExe pythonForRuntimeManifest} ${./make_icns.py} verify \
      "$appResources/icon.icns" \
      --reference "$stockIcon"

    ${lib.getExe python3} ${./verify_bundle_identity.py} \
      "$out/Applications/${appBundleName}/Contents/Info.plist" \
      --app-name ${lib.escapeShellArg appName} \
      --bundle-id ${lib.escapeShellArg appId} \
      --version ${lib.escapeShellArg appVersion} \
      --icon-file icon.icns \
      ${lib.concatMapStringsSep " \\\n      " (
        scheme: "--url-scheme ${lib.escapeShellArg scheme}"
      ) appProtocolSchemes}

    ${lib.getExe python3} ${../../lib/asar_integrity.py} \
      check-info-plist-hash \
      "$out/Applications/${appBundleName}/Contents/Info.plist" \
      "$appResources/app.asar"

    if [ ! -d "$appResources/app.asar.unpacked" ]; then
      echo "missing native module unpack directory for ${appBundleName} app.asar" >&2
      exit 1
    fi

    ELECTRON_RUN_AS_NODE=1 "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" <<'NODE'
    require(
      process.env.out
        + "/Applications/${appBundleName}/Contents/Resources/app.asar/node_modules/node-pty",
    );
    console.log("node-pty ok");
    const fs = require("node:fs");
    const os = require("node:os");
    const path = require("node:path");
    const { createRequire } = require("node:module");
    const root = process.env.out + "/Applications/${appBundleName}/Contents/Resources/app.asar";
    const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "t3code-native-check-"));
    // Resolve the way the packaged server does: through the app's own
    // node_modules entry, which pairs the JS loader with its dylib.
    const appRequire = createRequire(path.join(root, "package.json"));
    const { FileFinder } = appRequire("@ff-labs/fff-node");
    // The app selects its update channel from this value, so verify the channel
    // version actually reached the packaged manifest.
    const packagedVersion = JSON.parse(
      fs.readFileSync(path.join(root, "package.json"), "utf8"),
    ).version;
    if (packagedVersion !== "${appVersion}") {
      throw new Error(
        "packaged app version " + packagedVersion + " is not ${appVersion}",
      );
    }
    console.log("app version ok: " + packagedVersion);
    try {
      const result = FileFinder.create({
        basePath: temporary,
        watch: false,
        frecencyDbPath: path.join(temporary, "frecency"),
        historyDbPath: path.join(temporary, "history"),
      });
      if (!result.ok) throw new Error(result.error);
      result.value.destroy();
      console.log("fff native library ok");
    } finally {
      fs.rmSync(temporary, { recursive: true, force: true });
    }
    NODE

    ELECTRON_RUN_AS_NODE=1 "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" \
      "$out/Applications/${appBundleName}/Contents/Resources/app.asar/apps/server/dist/bin.mjs" \
      --help

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

    inherit (mkMacAppPassthru { bundleName = appBundleName; }) macApp;
  };

  meta = with lib; {
    description = "T3 Code desktop app";
    homepage = "https://github.com/pingdotgg/t3code";
    license = licenses.mit;
    mainProgram = pname;
    platforms = [ "aarch64-darwin" ];
  };
}
