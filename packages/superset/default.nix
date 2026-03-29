{
  inputs,
  outputs,
  stdenv,
  stdenvNoCC,
  lib,
  bun,
  bun2nix,
  appimageTools,
  fetchurl,
  python3,
  writeShellScriptBin,
  makeWrapper,
  ...
}:
let
  slib = outputs.lib;
  info = slib.sources.superset;
  version = slib.getFlakeVersion "superset";
  pname = "superset";
  upstreamSrc = inputs.superset;
  supportedPlatforms = [
    "aarch64-darwin"
    "x86_64-linux"
  ];
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

    nix run "path:$repo_root#nixcfg" -- ci workflow validate-bun-lock --lock-file "$tmpdir/bun.lock"

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

    postBunSetInstallCacheDirPhase = ''
      chmod -R u+w "$BUN_INSTALL_CACHE_DIR"
    '';

    postPatch = ''
      substituteInPlace package.json \
        --replace-fail '"postinstall": "./scripts/postinstall.sh"' '"postinstall": ""'

      substituteInPlace apps/desktop/electron-builder.ts \
        --replace-fail 'target: "default",' 'target: "dir",' \
        --replace-fail 'hardenedRuntime: true,' 'hardenedRuntime: false,' \
        --replace-fail 'notarize: true,' 'notarize: false,'
    '';

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR"
      export SKIP_ENV_VALIDATION=1
      export NEXT_PUBLIC_OUTLIT_KEY="nix-build"
      export CSC_IDENTITY_AUTO_DISCOVERY=false

      bun run --cwd apps/desktop copy:native-modules

      python3 - <<'PY'
      from pathlib import Path

      old = '"<!@(node -p \\\"require(\'node-addon-api\').include\\\")"'
      new = '"../../node-addon-api"'
      patched = []

      for path in Path("apps/desktop/node_modules").rglob("binding.gyp"):
          text = path.read_text()
          if old not in text:
              continue
          path.write_text(text.replace(old, new))
          patched.append(path)

      if patched:
          print("patched node-addon-api include paths in:")
          for path in patched:
              print(f"  {path}")
      else:
          print("no binding.gyp files needed node-addon-api include patching")
      PY

      bun run --cwd apps/desktop generate:icons
      bun run --cwd apps/desktop compile:app
      bun run --cwd apps/desktop validate:native-runtime
      bun run --cwd apps/desktop install:deps
      bun run --cwd apps/desktop package

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
      ln -s "$out/Applications/Superset.app/Contents/MacOS/Superset" "$out/bin/superset"

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

    passthru.updateScript = updateScript;

    meta = with lib; {
      description = "Desktop client for the Superset agent platform";
      homepage = "https://github.com/superset-sh/superset";
      license = licenses.asl20;
      platforms = supportedPlatforms;
      mainProgram = "superset";
    };
  }
