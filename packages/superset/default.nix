{
  inputs,
  outputs,
  stdenv,
  stdenvNoCC,
  lib,
  bun,
  bun2nix,
  electron,
  appimageTools,
  fetchurl,
  writeShellScriptBin,
  makeWrapper,
  ...
}:
let
  slib = outputs.lib;
  info = slib.sources.superset;
  version = slib.getFlakeVersion "superset";
  pname = "superset-desktop";
  upstreamSrc = inputs.superset;
  linuxAppImage = fetchurl {
    name = "superset-${info.version}-x86_64.AppImage";
    url = info.urls."x86_64-linux";
    hash = info.hashes."x86_64-linux";
  };
  updateScript = writeShellScriptBin "update-superset-bun-lock" ''
    set -euo pipefail

    if [ ! -f flake.nix ] || [ ! -d packages/superset ]; then
      echo "run this script from the nixcfg repository root" >&2
      exit 1
    fi

    repo_root="$(pwd)"
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' EXIT

    cp -R ${upstreamSrc}/. "$tmpdir"
    chmod -R u+w "$tmpdir"

    (
      cd "$tmpdir"
      nix run "${inputs.bun2nix}#bun2nix" -- \
        --lock-file bun.lock \
        --copy-prefix ./ \
        --output-file "$repo_root/packages/superset/bun.nix"
    )
  '';
  srcWithBun = stdenvNoCC.mkDerivation {
    pname = "superset-src-with-bun";
    inherit version;
    src = upstreamSrc;
    dontUnpack = true;
    installPhase = ''
      mkdir -p "$out"
      cp -R "$src"/. "$out"
      chmod -R u+w "$out"
      cp ${./bun.nix} "$out/bun.nix"
    '';
  };
  desktopDir = "$out/share/superset";
  # Electron externalizes these modules at runtime. Keep this list aligned
  # with apps/desktop/electron.vite.config.ts and validate-native-runtime.ts
  externalRuntimeModules = [
    "better-sqlite3"
    "node-pty"
    "@ast-grep/napi"
    "libsql"
    "@neon-rs/load"
    "detect-libc"
  ];
  platformLibsqlCandidates =
    if stdenv.hostPlatform.isDarwin then
      [
        (if stdenv.hostPlatform.isAarch64 then "@libsql/darwin-arm64" else "@libsql/darwin-x64")
      ]
    else if stdenv.hostPlatform.isLinux then
      if stdenv.hostPlatform.isAarch64 then
        [
          "@libsql/linux-arm64-gnu"
          "@libsql/linux-arm64-musl"
        ]
      else
        [
          "@libsql/linux-x64-gnu"
          "@libsql/linux-x64-musl"
        ]
    else
      [ ];
in
if stdenv.hostPlatform.isLinux then
  appimageTools.wrapType2 {
    inherit pname;
    inherit (info) version;
    src = linuxAppImage;

    extraInstallCommands =
      let
        appimageContents = appimageTools.extractType2 {
          inherit pname;
          inherit (info) version;
          src = linuxAppImage;
        };
      in
      ''
        if [ -d "${appimageContents}/usr/share" ]; then
          cp -r "${appimageContents}/usr/share" "$out/"
        fi
        ln -sf "$out/bin/${pname}" "$out/bin/superset"
      '';

    passthru.updateScript = updateScript;

    meta = with lib; {
      description = "Desktop client for the Superset agent platform";
      homepage = "https://github.com/superset-sh/superset";
      license = licenses.asl20;
      platforms = [ "x86_64-linux" ];
      sourceProvenance = with sourceTypes; [ binaryNativeCode ];
      mainProgram = "superset";
    };
  }
else
  stdenv.mkDerivation {
    inherit pname version;

    src = srcWithBun;

    nativeBuildInputs = [
      bun
      bun2nix.hook
      makeWrapper
    ];

    strictDeps = true;

    bunDeps = bun2nix.fetchBunDeps {
      bunNix = "${srcWithBun}/bun.nix";
    };

    bunInstallFlags = [
      "--frozen-lockfile"
    ]
    ++ lib.optionals (stdenv.hostPlatform.system == "x86_64-darwin") [
      "--linker=isolated"
      "--backend=symlink"
      "--cpu=*"
    ];

    postPatch = ''
      substituteInPlace package.json \
        --replace-fail '"postinstall": "./scripts/postinstall.sh"' '"postinstall": ""'
    '';

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR"
      export SKIP_ENV_VALIDATION=1
      export NEXT_PUBLIC_OUTLIT_KEY="nix-build"

      bun run --cwd apps/desktop copy:native-modules
      bun run --cwd apps/desktop compile:app
      bun run --cwd apps/desktop validate:native-runtime

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      mkdir -p ${desktopDir}
      cp -r apps/desktop/dist ${desktopDir}/dist
      mkdir -p ${desktopDir}/src
      cp -r apps/desktop/src/resources ${desktopDir}/src/resources
      cp apps/desktop/package.json ${desktopDir}/package.json

      runtimeModuleManifest="$TMPDIR/superset-runtime-modules.txt"
      bun --eval '
        import { existsSync, readFileSync } from "node:fs";
        import { join } from "node:path";

        const nodeModulesDir = process.argv[1];
        const queue = process.argv.slice(2);
        const seen = new Set();

        while (queue.length > 0) {
          const mod = queue.pop();
          if (!mod || seen.has(mod)) continue;

          const pkgJsonPath = join(nodeModulesDir, mod, "package.json");
          if (!existsSync(pkgJsonPath)) continue;

          seen.add(mod);
          const pkg = JSON.parse(readFileSync(pkgJsonPath, "utf8"));

          for (const dep of Object.keys(pkg.dependencies ?? {})) {
            if (!seen.has(dep)) queue.push(dep);
          }
          for (const dep of Object.keys(pkg.optionalDependencies ?? {})) {
            if (!seen.has(dep)) queue.push(dep);
          }
        }

        for (const mod of [...seen].sort()) {
          console.log(mod);
        }
      ' "apps/desktop/node_modules" ${lib.escapeShellArgs externalRuntimeModules} > "$runtimeModuleManifest"

      mkdir -p ${desktopDir}/node_modules
      while IFS= read -r mod; do
        if [ -e "apps/desktop/node_modules/$mod" ]; then
          mkdir -p "${desktopDir}/node_modules/$(dirname "$mod")"
          cp -R -L "apps/desktop/node_modules/$mod" "${desktopDir}/node_modules/$mod"
        fi
      done < "$runtimeModuleManifest"

      mkdir -p "$out/bin"
      makeWrapper ${electron}/bin/electron "$out/bin/superset" \
        --add-flags ${desktopDir}

      runHook postInstall
    '';

    doInstallCheck = true;
    installCheckPhase = ''
      runHook preInstallCheck

      requiredRuntimePaths="
        ${desktopDir}/dist/main/index.js
        ${desktopDir}/dist/preload/index.js
        ${desktopDir}/package.json
        ${desktopDir}/node_modules/better-sqlite3/package.json
        ${desktopDir}/node_modules/node-pty/package.json
        ${desktopDir}/node_modules/@ast-grep/napi/package.json
        ${desktopDir}/node_modules/libsql/package.json
        ${desktopDir}/node_modules/@neon-rs/load/package.json
        ${desktopDir}/node_modules/detect-libc/package.json
      "

      for path in $requiredRuntimePaths; do
        if [ ! -e "$path" ]; then
          echo "missing required runtime path: $path" >&2
          exit 1
        fi
      done

      libsql_platform_found=0
      for mod in ${lib.concatStringsSep " " platformLibsqlCandidates}; do
        if [ -e "${desktopDir}/node_modules/$mod/package.json" ]; then
          libsql_platform_found=1
        fi
      done

      if [ "$libsql_platform_found" -ne 1 ]; then
        echo "missing platform-specific @libsql runtime package in ${desktopDir}/node_modules" >&2
        exit 1
      fi

      runHook postInstallCheck
    '';

    passthru.updateScript = updateScript;

    meta = with lib; {
      description = "Desktop client for the Superset agent platform";
      homepage = "https://github.com/superset-sh/superset";
      license = licenses.asl20;
      platforms = [
        "aarch64-darwin"
        "x86_64-linux"
      ];
      mainProgram = "superset";
    };
  }
