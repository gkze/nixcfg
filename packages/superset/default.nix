{
  inputs,
  outputs,
  pkgs,
  selfSource,
  stdenv,
  stdenvNoCC,
  lib,
  nixcfgElectron,
  bun,
  bun2nix,
  appimageTools,
  fetchurl,
  libarchive,
  python3,
  makeWrapper,
  zig_0_15,
  ...
}:
let
  slib = outputs.lib;
  info = selfSource;
  version = slib.getFlakeVersion "superset";
  pname = "superset";
  upstreamSrc = inputs.superset;
  updateBunLockTemplate = ./update_bun_lock.py;
  extractBunPackageHelper = ./extract_bun_package.py;
  patchBindingGypHelper = ./patch_node_addon_api_binding_gyp.py;
  supportedPlatforms = [
    "aarch64-darwin"
    "x86_64-linux"
  ];
  linuxAppImage = fetchurl {
    name = "superset-${info.version}-x86_64.AppImage";
    url = info.urls."x86_64-linux";
    hash = info.hashes."x86_64-linux";
  };
  updateScript = pkgs.writeTextFile {
    name = "update-superset-bun-lock";
    destination = "/bin/update-superset-bun-lock";
    executable = true;
    text =
      "#!${lib.getExe python3}\n"
      +
        builtins.replaceStrings
          [
            "@UPSTREAM_SRC@"
            "@BUN@"
            "@BUN2NIX_FLAKE@"
          ]
          [
            (toString upstreamSrc)
            (lib.getExe bun)
            (toString inputs.bun2nix)
          ]
          (builtins.readFile updateBunLockTemplate);
  };
  # Keep this in sync with apps/desktop/package.json and bun.lock. Reuse the
  # centrally-packaged runtime and headers so Electron builder/rebuild stays
  # offline and shares cache entries with other Electron apps.
  electronVersion = "40.8.5";
  electronRuntime = nixcfgElectron.runtimeFor electronVersion;
  electronRuntimeVersion = electronRuntime.version;
  electronHeaders = electronRuntime.passthru.headers;
  electronDist = electronRuntime.passthru.dist;
  invalidBunNixErr = ''
    packages/superset/bun.nix failed to evaluate.

    Regenerate it with:

    ```sh
    bun2nix -o bun.nix
    ```
  '';
  extractHost =
    url:
    let
      match = builtins.match "https?://([^/]+).*" url;
    in
    if match != null then builtins.elemAt match 0 else null;
  bunWithFakeNode = stdenvNoCC.mkDerivation {
    name = "bun-with-fake-node";
    nativeBuildInputs = [ makeWrapper ];
    dontUnpack = true;
    dontBuild = true;

    installPhase = ''
      cp -r "${bun}/." "$out"
      chmod u+w "$out/bin"

      for node_binary in node npm bunx; do
        if [ -e "$out/bin/$node_binary" ]; then
          continue
        fi
        ln -s "$out/bin/bun" "$out/bin/$node_binary"
      done

      makeWrapper "$out/bin/bunx" "$out/bin/npx"
    '';
  };
  bunCacheEntryCreator = stdenvNoCC.mkDerivation {
    pname = "bun2nix-cache-entry-creator";
    inherit (bun2nix) version;
    src = inputs.bun2nix + "/programs/cache-entry-creator";

    nativeBuildInputs = [ zig_0_15.hook ];

    postConfigure = ''
      ln -s ${
        pkgs.callPackage (inputs.bun2nix + "/programs/cache-entry-creator/deps.nix") { }
      } $ZIG_GLOBAL_CACHE_DIR/p
    '';

    zigBuildFlags = [ "--release=fast" ];
    doCheck = true;

    meta = {
      description = "Cache entry creator for bun packages";
      mainProgram = "cache_entry_creator";
    };
  };
  srcWithBun = stdenvNoCC.mkDerivation {
    pname = "superset-src-with-bun";
    inherit version;
    src = upstreamSrc;
    dontUnpack = true;

    nativeBuildInputs = [ bun ];

    installPhase = ''
      mkdir -p "$out"
      cp -R "$src"/. "$out"
      chmod -R u+w "$out"
      cp ${./bun.lock} "$out/bun.lock"
      cp ${./align-package-json.ts} "$out/align-package-json.ts"
      bun "$out/align-package-json.ts"
      cp ${./bun.nix} "$out/bun.nix"
    '';
  };
  bunDeps =
    let
      bunPackages = lib.filterAttrs (_: value: lib.isStorePath value) (
        builtins.addErrorContext invalidBunNixErr (pkgs.callPackage "${srcWithBun}/bun.nix" { })
      );
      buildBunPackage =
        name: pkg:
        let
          pkgUrl = pkg.passthru.url or null;
          registryHost =
            if pkgUrl != null then
              let
                host = extractHost pkgUrl;
              in
              if host != null && host != "registry.npmjs.org" then host else null
            else
              null;
        in
        stdenv.mkDerivation {
          name = "bun-pkg-${name}";

          nativeBuildInputs = [ bunWithFakeNode ];
          phases = [
            "extractPhase"
            "patchPhase"
            "cacheEntryPhase"
          ];

          extractPhase = ''
            runHook preExtract

            ${lib.getExe python3} ${extractBunPackageHelper} \
              --bsdtar ${lib.escapeShellArg (lib.getExe' libarchive "bsdtar")} \
              --package "${pkg}" \
              --out "$out/share/bun-packages/${name}"

            runHook postExtract
          '';

          patchPhase = ''
            runHook prePatch

            patchShebangs "$out/share/bun-packages"

            runHook postPatch
          '';

          cacheEntryPhase = ''
            runHook preCacheEntry

            "${lib.getExe bunCacheEntryCreator}" \
              --out "$out/share/bun-cache" \
              --name "${name}" \
              --package "$out/share/bun-packages/${name}" \
              ${lib.optionalString (registryHost != null) ''
                --registry "${registryHost}"
              ''}

            runHook postCacheEntry
          '';

          preferLocalBuild = true;
          allowSubstitutes = false;
        };
    in
    pkgs.symlinkJoin {
      name = "bun-cache";
      paths = builtins.attrValues (builtins.mapAttrs buildBunPackage bunPackages);
    };
  # Electron externalizes these modules at runtime. Keep this list aligned
  # with apps/desktop/electron.vite.config.ts and validate-native-runtime.ts
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
      platforms = supportedPlatforms;
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
      python3
    ];

    strictDeps = true;

    inherit bunDeps;

    bunInstallFlags = [
      "--frozen-lockfile"
    ]
    ++ lib.optionals (stdenv.hostPlatform.system == "x86_64-darwin") [
      "--linker=isolated"
      "--backend=symlink"
      "--cpu=*"
    ];

    postBunSetInstallCacheDirPhase = ''
      chmod -R u+w "$BUN_INSTALL_CACHE_DIR"
    '';

    postPatch = ''
      substituteInPlace package.json \
        --replace-fail '"postinstall": "./scripts/postinstall.sh"' '"postinstall": ""'
    '';

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR"
      export SKIP_ENV_VALIDATION=1
      export NEXT_PUBLIC_OUTLIT_KEY="nix-build"
      export CSC_IDENTITY_AUTO_DISCOVERY=false
      export ELECTRON_SKIP_BINARY_DOWNLOAD=1
      export npm_config_runtime=electron
      export npm_config_target=${electronVersion}

      export npm_config_nodedir=${lib.escapeShellArg (toString electronHeaders)}

      bun run --cwd apps/desktop copy:native-modules

      ${lib.getExe python3} ${patchBindingGypHelper} \
        apps/desktop/node_modules

      bun run --cwd apps/desktop generate:icons
      bun run --cwd apps/desktop compile:app
      bun run --cwd apps/desktop validate:native-runtime
      bun run --cwd apps/desktop install:deps

      electronDistDir="$PWD/electron-dist"
      mkdir -p "$electronDistDir"
      cp -R ${electronDist}/. "$electronDistDir"/
      chmod -R u+w "$electronDistDir"

      (
        cd apps/desktop
        bun x electron-builder \
          --config electron-builder.ts \
          --mac \
          --dir \
          --publish never \
          -c.electronDist="$electronDistDir" \
          -c.electronVersion=${lib.escapeShellArg electronRuntimeVersion} \
          -c.mac.identity=null \
          -c.mac.hardenedRuntime=false \
          -c.mac.notarize=false
      )

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      appBundle="$(printf '%s\n' apps/desktop/release/mac*/Superset.app | head -n 1)"
      if [ ! -d "$appBundle" ]; then
        echo "failed to locate packaged Superset.app in apps/desktop/release" >&2
        exit 1
      fi

      mkdir -p "$out/Applications"
      cp -R "$appBundle" "$out/Applications/Superset.app"

      mkdir -p "$out/bin"
      ln -s "$out/Applications/Superset.app/Contents/MacOS/Superset" \
        "$out/bin/superset"

      runHook postInstall
    '';

    doInstallCheck = true;
    installCheckPhase = ''
      runHook preInstallCheck

      requiredRuntimePaths="
        $out/Applications/Superset.app
        $out/Applications/Superset.app/Contents/MacOS/Superset
        $out/bin/superset
      "

      for path in $requiredRuntimePaths; do
        if [ ! -e "$path" ]; then
          echo "missing required runtime path: $path" >&2
          exit 1
        fi
      done

      runHook postInstallCheck
    '';

    passthru = {
      inherit
        electronDist
        electronHeaders
        electronRuntime
        electronRuntimeVersion
        electronVersion
        updateScript
        ;
    };

    meta = with lib; {
      description = "Desktop client for the Superset agent platform";
      homepage = "https://github.com/superset-sh/superset";
      license = licenses.asl20;
      platforms = supportedPlatforms;
      mainProgram = "superset";
    };
  }
