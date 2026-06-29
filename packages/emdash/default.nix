{
  autoconf,
  automake,
  cctools,
  coreutils,
  dpkg,
  fetchPnpmDeps ? null,
  git,
  inputs,
  lib,
  libiconv,
  libsecret,
  libutempter,
  nodejs_24,
  openssl,
  outputs,
  patchelf,
  pkg-config,
  pnpmConfigHook,
  pnpm_10,
  nixcfgElectron,
  python3,
  rpm,
  sqlite,
  stdenv,
  zlib,
  ...
}:
let
  pname = "emdash";
  appDir = "apps/emdash-desktop";
  slib = outputs.lib;
  version = slib.getFlakeVersion pname;
  src = inputs.emdash;
  nodejs = nodejs_24;
  pnpm = pnpm_10.override { inherit nodejs; };
  inherit (stdenv.hostPlatform) system;
  npmDepsHash =
    let
      perPlatformHash = builtins.tryEval (slib.sourceHashForPlatform pname "npmDepsHash" system);
    in
    if perPlatformHash.success then perPlatformHash.value else slib.sourceHash pname "npmDepsHash";

  electronVersion = "40.7.0";
  electronBuild = nixcfgElectron.sourceBuildFor electronVersion;
  electronRuntime = electronBuild.runtime;
  electronRuntimeVersion = electronBuild.runtimeVersion;
  electronHeaders = electronBuild.headers;
  electronDist = electronBuild.dist;
  supportedSystems = [
    "aarch64-darwin"
    "aarch64-linux"
    "x86_64-linux"
  ];
  electronBuilderTarget = if stdenv.hostPlatform.isDarwin then "mac" else "linux";
  patchNodeAddonApi = ./patch_node_addon_api.py;
  materializeAsarNodeModules = ./materialize_asar_node_modules.cjs;
  msShim = ./ms-shim.cjs;

  pnpmDeps =
    if fetchPnpmDeps != null then
      fetchPnpmDeps {
        inherit
          pname
          version
          src
          pnpm
          ;
        fetcherVersion = 3;
        hash = npmDepsHash;
      }
    else
      pnpm.fetchDeps {
        inherit
          pname
          version
          src
          ;
        fetcherVersion = 3;
        hash = npmDepsHash;
      };
in
stdenv.mkDerivation {
  inherit
    pname
    version
    src
    pnpmDeps
    ;

  nativeBuildInputs = [
    autoconf
    automake
    coreutils
    git
    nodejs
    pkg-config
    pnpm
    pnpmConfigHook
    python3
  ]
  ++ lib.optionals stdenv.hostPlatform.isDarwin [
    cctools
    libiconv
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [
    dpkg
    patchelf
    rpm
  ];

  buildInputs = [
    openssl
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [
    libsecret
    libutempter
    sqlite
    zlib
  ];

  strictDeps = true;

  env = electronBuild.commonEnv // {
    CI = "1";
    EMDASH_NIXCFG_BUILD_REV = "3";
    npm_config_build_from_source = "true";
    npm_config_manage_package_manager_versions = "false";
    npm_config_node_linker = "hoisted";
  };

  postPatch = ''
    substituteInPlace ${appDir}/src/main/utils/userEnv.ts \
      --replace-fail " -ilc 'env'" " -lc 'env'"
  '';

  buildPhase = ''
    runHook preBuild

    export HOME="$TMPDIR/emdash-home"
    mkdir -p "$HOME"
    pnpm config set manage-package-manager-versions false

    rm -rf ${appDir}/node_modules
    ln -s ../../node_modules ${appDir}/node_modules

    mkdir -p node_modules/@emdash
    workspace_packages=(
      chat-ui
      core
      plugins
      shared
      ui
    )
    for workspace_package in "''${workspace_packages[@]}"
    do
      ln -sfn "../../packages/$workspace_package" \
        "node_modules/@emdash/$workspace_package"
    done

    pnpm --filter @emdash/shared run build
    pnpm --filter @emdash/core run build
    pnpm --filter @emdash/plugins run build
    pnpm --filter @emdash/chat-ui run build
    pnpm --filter @emdash/ui run build

    for workspace_package in "''${workspace_packages[@]}"
    do
      rm "node_modules/@emdash/$workspace_package"
      cp -R "packages/$workspace_package" \
        "node_modules/@emdash/$workspace_package"
    done

    pushd ${appDir}

    # Work around keytar's bundled node-addon-api constant-expression issue
    # with the Apple toolchain in this build environment.
    PYTHONPATH=${../..} ${lib.getExe python3} ${patchNodeAddonApi}

    # pnpmConfigHook installs dependencies without relying on upstream
    # postinstall scripts, so rebuild native Electron modules explicitly.
    pnpm exec electron-rebuild -f -v ${electronVersion} --only=better-sqlite3,node-pty

    pnpm run build

    install -Dm644 ${msShim} out/main/ms-shim.cjs
    install -Dm644 ${msShim} ../../out/main/ms-shim.cjs

    substituteInPlace node_modules/debug/src/common.js \
      --replace-fail "require('ms')" \
        "require('../../../out/main/ms-shim.cjs')"

    bundled_entrypoints=(
      node_modules/@octokit/request/dist-bundle/index.js
      node_modules/@gitbeaker/requester-utils/dist/index.mjs
      node_modules/@gitbeaker/core/dist/index.mjs
      node_modules/@gitbeaker/rest/dist/index.mjs
    )

    for bundled_entrypoint in "''${bundled_entrypoints[@]}"
    do
      pnpm exec esbuild \
        "$bundled_entrypoint" \
        --bundle \
        --platform=node \
        --format=esm \
        --banner:js=${lib.escapeShellArg "import { createRequire as __nixcfgCreateRequire } from 'node:module'; const require = __nixcfgCreateRequire(import.meta.url);"} \
        --outfile="$bundled_entrypoint.nixcfg-bundled"
    done

    for bundled_entrypoint in "''${bundled_entrypoints[@]}"
    do
      mv "$bundled_entrypoint.nixcfg-bundled" "$bundled_entrypoint"
    done

    ${electronBuild.copyDist}

    extra_electron_builder_flags=()
    ${lib.optionalString stdenv.hostPlatform.isDarwin ''
      extra_electron_builder_flags+=(
        --config electron-builder.config.ts
        -c.directories.output=dist
      )
      extra_electron_builder_flags+=(-c.mac.identity=null)
    ''}

    pnpm exec electron-builder \
      --${electronBuilderTarget} \
      --dir \
      --publish never \
      ${electronBuild.electronBuilderConfigFlags} \
      -c.npmRebuild=false \
      "''${extra_electron_builder_flags[@]}"

    ${lib.optionalString stdenv.hostPlatform.isDarwin ''
      app_resources="$PWD/dist/mac-arm64/Emdash.app/Contents/Resources"
      app_info_plist="$PWD/dist/mac-arm64/Emdash.app/Contents/Info.plist"
      asar_dir="$TMPDIR/emdash-app-asar"
      rm -rf "$asar_dir"
      pnpm exec asar extract "$app_resources/app.asar" "$asar_dir"

      EMDASH_ASAR_DIR="$asar_dir" ${lib.getExe nodejs} ${materializeAsarNodeModules}

      old_asar="$app_resources/app.asar.nixcfg-before-deps"
      old_unpacked="$app_resources/app.asar.unpacked"
      mv "$app_resources/app.asar" "$old_asar"
      rm -rf "$old_unpacked"
      pnpm exec asar pack "$asar_dir" "$app_resources/app.asar" --unpack "*.node"
      ${lib.getExe python3} ${../../lib/asar_integrity.py} \
        set-info-plist-hash "$app_info_plist" "$app_resources/app.asar"
      rm "$old_asar"
      rm -rf "$asar_dir"
    ''}

    popd
    runHook postBuild
  '';

  installPhase =
    if stdenv.hostPlatform.isDarwin then
      ''
        runHook preInstall

        distDir="$PWD/${appDir}/dist"
        appDir="$distDir/mac-arm64/Emdash.app"

        if [ ! -d "$appDir" ]; then
          echo \
            "Expected Emdash.app output from electron-builder, got nothing at $appDir" \
            >&2
          exit 1
        fi

        install -d "$out/Applications"
        cp -R "$appDir" "$out/Applications/"

        install -d "$out/bin"
        install -m755 ${./launcher-darwin.sh} "$out/bin/emdash"
        substituteInPlace "$out/bin/emdash" \
          --replace-fail "#!/usr/bin/env bash" "#!${stdenv.shell}" \
          --replace-fail "@out@" "$out"

        runHook postInstall
      ''
    else
      ''
        runHook preInstall

        distDir="$PWD/${appDir}/dist"
        shopt -s nullglob
        unpackedDirs=("$distDir"/linux*-unpacked)
        if [ "''${#unpackedDirs[@]}" -ne 1 ]; then
          printf \
            'Expected exactly one linux*-unpacked output from electron-builder, found %s\n' \
            "''${#unpackedDirs[@]}" \
            >&2
          exit 1
        fi
        unpackedDir="''${unpackedDirs[0]}"

        install -d "$out/share/emdash"
        cp -R "$unpackedDir" "$out/share/emdash/linux-unpacked"

        install -d "$out/bin"
        install -m755 ${./launcher-linux.sh} "$out/bin/emdash"
        substituteInPlace "$out/bin/emdash" \
          --replace-fail "#!/usr/bin/env bash" "#!${stdenv.shell}" \
          --replace-fail "@out@" "$out"

        runHook postInstall
      '';

  doInstallCheck = stdenv.hostPlatform.isDarwin;

  installCheckPhase = ''
    runHook preInstallCheck

    ${lib.getExe python3} ${../../lib/asar_integrity.py} \
      check-info-plist-hash \
      "$out/Applications/Emdash.app/Contents/Info.plist" \
      "$out/Applications/Emdash.app/Contents/Resources/app.asar"

    if [ ! -d "$out/Applications/Emdash.app/Contents/Resources/app.asar.unpacked" ]; then
      echo "missing native module unpack directory for Emdash app.asar" >&2
      exit 1
    fi

    ELECTRON_RUN_AS_NODE=1 "$out/Applications/Emdash.app/Contents/MacOS/Emdash" <<'NODE'
    const nodeModules =
      process.env.out + "/Applications/Emdash.app/Contents/Resources/app.asar/node_modules";

    const entrypoints = [
      {
        packagePath: "@octokit/request/dist-bundle/index.js",
        success: "octokit request ok",
      },
      {
        packagePath: "@gitbeaker/requester-utils/dist/index.mjs",
        success: "gitbeaker requester ok",
      },
      {
        packagePath: "@gitbeaker/core/dist/index.mjs",
        success: "gitbeaker core ok",
      },
      {
        packagePath: "@gitbeaker/rest/dist/index.mjs",
        success: "gitbeaker rest ok",
      },
    ];

    (async () => {
      for (const entrypoint of entrypoints) {
        await import("file://" + nodeModules + "/" + entrypoint.packagePath);
        console.log(entrypoint.success);
      }
    })().catch((error) => {
        console.error(error);
        process.exit(1);
      });
    NODE

    ELECTRON_RUN_AS_NODE=1 "$out/Applications/Emdash.app/Contents/MacOS/Emdash" <<'NODE'
    const fs = require("fs");
    const path = require("path");

    const nodeModules = path.join(
      process.env.out,
      "Applications/Emdash.app/Contents/Resources/app.asar/node_modules",
    );

    function packagePath(base, packageName) {
      return path.join(base, ...packageName.split("/"));
    }

    function packageDirs(base) {
      const dirs = [];
      for (const entry of fs.readdirSync(base, { withFileTypes: true })) {
        if (!entry.isDirectory() || entry.name.startsWith(".")) {
          continue;
        }

        const entryPath = path.join(base, entry.name);
        if (entry.name.startsWith("@")) {
          for (const scopedEntry of fs.readdirSync(entryPath, { withFileTypes: true })) {
            if (scopedEntry.isDirectory()) {
              dirs.push(path.join(entryPath, scopedEntry.name));
            }
          }
        } else {
          dirs.push(entryPath);
        }
      }
      return dirs;
    }

    function packageDependencies(packageJson) {
      return Object.keys(packageJson.dependencies || {});
    }

    const missing = [];
    for (const packageDir of packageDirs(nodeModules)) {
      const packageJsonPath = path.join(packageDir, "package.json");
      if (!fs.existsSync(packageJsonPath)) {
        continue;
      }

      const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
      for (const packageName of packageDependencies(packageJson)) {
        if (!fs.existsSync(path.join(packagePath(nodeModules, packageName), "package.json"))) {
          missing.push(packageJson.name + " -> " + packageName);
        }
      }
    }

    if (missing.length > 0) {
      throw new Error(
        "missing packaged node module dependencies:\n" + missing.slice(0, 50).join("\n"),
      );
    }

    console.log("package dependency closure ok");
    NODE

    ELECTRON_RUN_AS_NODE=1 "$out/Applications/Emdash.app/Contents/MacOS/Emdash" <<'NODE'
    const nodeModules =
      process.env.out + "/Applications/Emdash.app/Contents/Resources/app.asar/node_modules";

    for (const packageName of ["node-pty", "better-sqlite3", "@parcel/watcher"]) {
      require(nodeModules + "/" + packageName);
      console.log(packageName + " ok");
    }
    NODE

    ELECTRON_RUN_AS_NODE=1 "$out/Applications/Emdash.app/Contents/MacOS/Emdash" <<'NODE'
    require(
      process.env.out
        + "/Applications/Emdash.app/Contents/Resources/app.asar/node_modules/form-data/lib/form_data.js",
    );
    console.log("form-data ok");
    NODE

    ${lib.getExe python3} - "$out/Applications/Emdash.app/Contents/Info.plist" <<'PY'
    import plistlib
    import sys

    expected = {
        "CFBundleDisplayName": "Emdash",
        "CFBundleExecutable": "Emdash",
        "CFBundleIconFile": "icon.icns",
        "CFBundleIdentifier": "com.emdash.stable",
    }

    with open(sys.argv[1], "rb") as plist_file:
        plist = plistlib.load(plist_file)

    for key, expected_value in expected.items():
        actual_value = plist.get(key)
        if actual_value != expected_value:
            raise SystemExit(
                "%s expected %r, got %r" % (key, expected_value, actual_value)
            )
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
      ;
    macApp = {
      bundleName = "Emdash.app";
      bundleRelPath = "Applications/Emdash.app";
      installMode = "copy";
    };
  };

  meta = with lib; {
    description = "Agentic development environment for parallel coding agents";
    homepage = "https://github.com/generalaction/emdash";
    license = licenses.mit;
    platforms = supportedSystems;
    mainProgram = pname;
  };
}
