{
  bun,
  inputs,
  lib,
  makeDesktopItem,
  models-dev,
  nixcfgElectron,
  nodejs,
  opencode,
  outputs,
  python3,
  stdenv,
  pname ? "opencode-desktop-electron",
  sourceHashPackageName ? "opencode-desktop-electron",
  opencodeChannel ? "prod",
  appName ? if opencodeChannel == "prod" then "OpenCode" else "OpenCode Dev",
  appId ? if opencodeChannel == "prod" then "ai.opencode.desktop" else "ai.opencode.desktop.dev",
  appProtocolName ? appName,
  appProtocolScheme ? "opencode",
  packageDescription ? "OpenCode Desktop Electron app",
  ...
}:
let
  inherit (builtins)
    fromJSON
    readFile
    ;

  inherit (stdenv.hostPlatform) system;

  slib = outputs.lib;
  inherit (opencode) version;
  inherit (opencode) src;
  repoRoot = ../..;
  opencodeOverlayDir = repoRoot + "/overlays/opencode";
  appBundleName = "${appName}.app";
  appExecutableName = appName;

  desktopPackageJson = fromJSON (readFile "${src}/packages/desktop-electron/package.json");
  desktopPackageVersion = desktopPackageJson.version;
  electronVersion = lib.removePrefix "^" desktopPackageJson.devDependencies.electron;
  electronRuntime = nixcfgElectron.runtimeFor electronVersion;
  electronRuntimeVersion = electronRuntime.version;

  desktopPackageVersionCheck =
    if
      version == desktopPackageVersion
      || lib.hasPrefix "${desktopPackageVersion}-" version
      || lib.hasPrefix "${desktopPackageVersion}+" version
    then
      true
    else
      throw ''
        packages/opencode-desktop-electron/default.nix has desktop version ${version},
        expected ${desktopPackageVersion}, ${desktopPackageVersion}-<suffix>, or ${desktopPackageVersion}+<build-metadata>
      '';

  electronRuntimeVersionCheck =
    if electronRuntimeVersion == electronVersion then
      true
    else
      throw ''
        packages/opencode-desktop-electron/default.nix needs Electron ${electronVersion},
        but the selected runtime is ${electronRuntimeVersion}; add the exact runtime to nixcfgElectron
      '';

  bunTarget =
    {
      aarch64-darwin = {
        cpu = "arm64";
        os = "darwin";
      };
      x86_64-darwin = {
        cpu = "x64";
        os = "darwin";
      };
      aarch64-linux = {
        cpu = "arm64";
        os = "linux";
      };
      x86_64-linux = {
        cpu = "x64";
        os = "linux";
      };
    }
    .${system} or (throw "Unsupported system ${system} for ${pname}");

  electronDist = electronRuntime.passthru.dist;

  node_modules = opencode.node_modules.overrideAttrs (old: {
    pname = "${pname}-node_modules";
    nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ python3 ];
    preBuild = (old.preBuild or "") + ''
      # Bun re-resolves the branch-based ghostty-web dependency and mutates
      # bun.lock even under --frozen-lockfile. Pin the workspace manifest to the
      # commit already recorded in bun.lock so the install stays reproducible.
      ${lib.getExe python3} ${ghosttyWebPin} .
    '';
    outputHash = slib.sourceHashForPlatform sourceHashPackageName "nodeModulesHash" system;
    buildPhase = ''
      runHook preBuild

      export BUN_INSTALL_CACHE_DIR="$(mktemp -d)"
      # desktop-electron shells into packages/opencode during prepare/build and
      # pulls renderer code from app/ui/core, SDK helpers from packages/sdk/js,
      # and version metadata helpers from packages/script.
      bun install \
        --cpu="${bunTarget.cpu}" \
        --os="${bunTarget.os}" \
        --filter '!./' \
        --filter './packages/opencode' \
        --filter './packages/desktop-electron' \
        --filter './packages/app' \
        --filter './packages/core' \
        --filter './packages/shared' \
        --filter './packages/ui' \
        --filter './packages/sdk/js' \
        --filter './packages/script' \
        --frozen-lockfile \
        --ignore-scripts \
        --no-progress

      if [ -d node_modules/.bun/node_modules ]; then
        bun --bun ${inputs.opencode}/nix/scripts/canonicalize-node-modules.ts
        bun --bun ${inputs.opencode}/nix/scripts/normalize-bun-binaries.ts
      fi

      runHook postBuild
    '';
    installPhase = ''
      runHook preInstall

      mkdir -p "$out"
      cp -R node_modules "$out/node_modules"

      # Only copy the workspace-local node_modules trees that the filtered
      # install materialized. Re-copying every nested .bun/*/node_modules
      # directory duplicates Bun's symlink farm and hits create_symlink EEXIST
      # on Darwin during fixed-output hash computation.
      for workspace in \
        packages/opencode \
        packages/desktop-electron \
        packages/app \
        packages/core \
        packages/shared \
        packages/ui \
        packages/sdk/js \
        packages/script
      do
        if [ -d "$workspace/node_modules" ]; then
          cp -R --parents "$workspace/node_modules" "$out"
        fi
      done

      runHook postInstall
    '';
  });

  packageManagerSync = opencodeOverlayDir + "/sync_package_manager_bun_version.py";
  ghosttyWebPin = opencodeOverlayDir + "/pin_ghostty_web_ref.py";
  hoistedOpentuiLinker = opencodeOverlayDir + "/link-hoisted-opentui-packages.sh";
  linuxAppDirName = "${pname}";
  linuxDesktopItem = makeDesktopItem {
    name = pname;
    desktopName = appName;
    comment = packageDescription;
    exec = "${pname} %U";
    icon = pname;
    categories = [ "Development" ];
    startupWMClass = appName;
    mimeTypes = [ "x-scheme-handler/${appProtocolScheme}" ];
  };
in
assert desktopPackageVersionCheck;
assert electronRuntimeVersionCheck;
stdenv.mkDerivation {
  inherit
    pname
    version
    src
    ;

  nativeBuildInputs = [
    bun
    nodejs
    python3
  ];

  strictDeps = true;

  env = {
    CI = "1";
    CSC_IDENTITY_AUTO_DISCOVERY = "false";
    ELECTRON_SKIP_BINARY_DOWNLOAD = "1";
    MODELS_DEV_API_JSON = "${models-dev}/dist/_api.json";
    NODE_OPTIONS = "--max-old-space-size=6144";
    OPENCODE_APP_ID = appId;
    OPENCODE_APP_NAME = appName;
    OPENCODE_CHANNEL = opencodeChannel;
    OPENCODE_DISABLE_MODELS_FETCH = "true";
    OPENCODE_PROTOCOL_NAME = appProtocolName;
    OPENCODE_PROTOCOL_SCHEME = appProtocolScheme;
    OPENCODE_VERSION = version;
  };

  postUnpack = ''
    chmod -R u+w source
  '';

  postPatch = ''
    bunVersion="$(bun -v | tr -d '\n')"
    ${lib.getExe python3} ${packageManagerSync} . "$bunVersion"
    ${lib.getExe python3} ${ghosttyWebPin} .

    if [ ! -f .github/TEAM_MEMBERS ]; then
      mkdir -p .github
      if [ -f ${inputs.opencode}/.github/TEAM_MEMBERS ]; then
        cp ${inputs.opencode}/.github/TEAM_MEMBERS .github/TEAM_MEMBERS
      else
        touch .github/TEAM_MEMBERS
      fi
    fi

    # @opencode-ai/script does a semver-based Bun version check at import
    # time, which requires the semver npm package. The filtered bun install
    # with --filter '!./' does not hoist semver@7 into node_modules, so any
    # transitive import of @opencode-ai/script fails at build time.
    #
    # Patch every build-time script that imports @opencode-ai/script to read
    # the derivation-provided env vars directly instead.
    substituteInPlace packages/desktop-electron/scripts/prepare.ts \
      --replace-fail 'import { Script } from "@opencode-ai/script"' 'const Script = { version: process.env.OPENCODE_VERSION ?? "0.0.0" }'

    # build-node.ts is invoked via prebuild.ts (cd ../opencode && bun
    # script/build-node.ts) and uses Script.channel to define
    # OPENCODE_CHANNEL in the bundled output.
    substituteInPlace packages/opencode/script/build-node.ts \
      --replace-fail 'import { Script } from "@opencode-ai/script"' 'const Script = { channel: process.env.OPENCODE_CHANNEL ?? "dev" }'

    # Keep desktop project icons stable unless the user explicitly sets one.
    # Upstream currently forces favicon auto-discovery on in the Electron shell,
    # which can replace the normal initial/color avatar with arbitrary repo
    # favicons on each launch.
    substituteInPlace packages/desktop-electron/src/main/server.ts \
      --replace-fail '    OPENCODE_EXPERIMENTAL_ICON_DISCOVERY: "true",' '    OPENCODE_EXPERIMENTAL_ICON_DISCOVERY: "false",'

    # Keep the packaged Electron runtime identity aligned with the Nix-level
    # overrides so the app bundle, userData path, and deep-link scheme agree.
    substituteInPlace packages/desktop-electron/src/main/index.ts \
      --replace-fail 'const appId = app.isPackaged ? APP_IDS[CHANNEL] : "ai.opencode.desktop.dev"' 'const appId = process.env.OPENCODE_APP_ID ?? (app.isPackaged ? APP_IDS[CHANNEL] : "ai.opencode.desktop.dev")' \
      --replace-fail 'app.setName(app.isPackaged ? APP_NAMES[CHANNEL] : "OpenCode Dev")' 'app.setName(process.env.OPENCODE_APP_NAME ?? (app.isPackaged ? APP_NAMES[CHANNEL] : "OpenCode Dev"))' \
      --replace-fail 'const urls = argv.filter((arg: string) => arg.startsWith("opencode://"))' 'const urls = argv.filter((arg: string) => arg.startsWith((process.env.OPENCODE_PROTOCOL_SCHEME ?? "opencode") + "://"))' \
      --replace-fail '    app.setAsDefaultProtocolClient("opencode")' '    app.setAsDefaultProtocolClient(process.env.OPENCODE_PROTOCOL_SCHEME ?? "opencode")'

    substituteInPlace packages/desktop-electron/electron-builder.config.ts \
      --replace-fail '    name: "OpenCode",' '    name: process.env.OPENCODE_PROTOCOL_NAME ?? "OpenCode",' \
      --replace-fail '    schemes: ["opencode"],' '    schemes: [process.env.OPENCODE_PROTOCOL_SCHEME ?? "opencode"],'

    mkdir -p packages/desktop-electron/native
  '';

  buildPhase =
    if stdenv.hostPlatform.isDarwin then
      ''
        runHook preBuild

        export HOME="$TMPDIR/home"
        mkdir -p "$HOME"

        electronDistDir="$PWD/electron-dist"
        mkdir -p "$electronDistDir"
        cp -R ${electronDist}/. "$electronDistDir"/
        chmod -R u+w "$electronDistDir"

        cp -a ${node_modules}/{node_modules,packages} .
        chmod -R u+w node_modules packages
        patchShebangs node_modules
        patchShebangs packages/*/node_modules

        ${stdenv.shell} ${hoistedOpentuiLinker}

        if [ -e packages/opencode/node_modules/glob ] && [ ! -e packages/shared/node_modules/glob ]; then
          mkdir -p packages/shared/node_modules
          chmod u+w packages/shared/node_modules
          ln -s ../../opencode/node_modules/glob packages/shared/node_modules/glob
        fi

        (
          cd packages/desktop-electron
          bun ./scripts/prepare.ts
          bun run build
          bun x electron-builder \
            --mac \
            --dir \
            --publish never \
            --config electron-builder.config.ts \
            -c.productName=${lib.escapeShellArg appName} \
            -c.appId=${lib.escapeShellArg appId} \
            -c.electronDist="$electronDistDir" \
            -c.electronVersion=${electronRuntimeVersion} \
            -c.mac.identity=null \
            -c.mac.hardenedRuntime=false \
            -c.mac.notarize=false \
            -c.npmRebuild=false
        )

        runHook postBuild
      ''
    else
      ''
        runHook preBuild

        export HOME="$TMPDIR/home"
        mkdir -p "$HOME"

        cp -a ${node_modules}/{node_modules,packages} .
        chmod -R u+w node_modules packages
        patchShebangs node_modules
        patchShebangs packages/*/node_modules

        ${stdenv.shell} ${hoistedOpentuiLinker}

        if [ -e packages/opencode/node_modules/glob ] && [ ! -e packages/shared/node_modules/glob ]; then
          mkdir -p packages/shared/node_modules
          chmod u+w packages/shared/node_modules
          ln -s ../../opencode/node_modules/glob packages/shared/node_modules/glob
        fi

        (
          cd packages/desktop-electron
          bun ./scripts/prepare.ts
          bun run build
          bun x electron-builder \
            --linux \
            --dir \
            --publish never \
            --config electron-builder.config.ts \
            -c.productName=${lib.escapeShellArg appName} \
            -c.appId=${lib.escapeShellArg appId} \
            -c.electronDist=${electronDist} \
            -c.electronVersion=${electronRuntimeVersion} \
            -c.npmRebuild=false
        )

        runHook postBuild
      '';

  installPhase =
    if stdenv.hostPlatform.isDarwin then
      ''
        runHook preInstall

        appBundle=""
        for appDir in packages/desktop-electron/dist/mac*; do
          candidate="$appDir/${appBundleName}"
          if [ -d "$candidate" ]; then
            appBundle="$candidate"
            break
          fi
        done

        if [ -z "$appBundle" ]; then
          echo "failed to locate packaged ${appBundleName} in packages/desktop-electron/dist" >&2
          exit 1
        fi

        mkdir -p "$out/Applications" "$out/bin"
        cp -R "$appBundle" "$out/Applications/${appBundleName}"
        ln -s "$out/Applications/${appBundleName}/Contents/MacOS/${appExecutableName}" "$out/bin/${pname}"

        runHook postInstall
      ''
    else
      ''
        runHook preInstall

        appDir=""
        for candidate in packages/desktop-electron/dist/linux*-unpacked; do
          if [ -d "$candidate" ]; then
            appDir="$candidate"
            break
          fi
        done

        if [ -z "$appDir" ]; then
          echo "failed to locate packaged Linux app in packages/desktop-electron/dist" >&2
          exit 1
        fi

        appBinary="$appDir/${appExecutableName}"
        if [ ! -x "$appBinary" ]; then
          appBinary=""
          shopt -s nullglob
          for candidate in "$appDir"/*; do
            if [ ! -f "$candidate" ] || [ ! -x "$candidate" ]; then
              continue
            fi
            case "$(basename "$candidate")" in
              chrome-sandbox|chrome_crashpad_handler)
                continue
                ;;
            esac
            appBinary="$candidate"
            break
          done
        fi

        if [ -z "$appBinary" ]; then
          echo "failed to locate packaged Linux executable in $appDir" >&2
          exit 1
        fi

        mkdir -p "$out/lib" "$out/bin" "$out/share/applications"
        cp -a "$appDir" "$out/lib/${linuxAppDirName}"
        ln -s "$out/lib/${linuxAppDirName}/$(basename "$appBinary")" "$out/bin/${pname}"

        if [ -f "$out/lib/${linuxAppDirName}/resources/icons/icon.png" ]; then
          install -Dm644 \
            "$out/lib/${linuxAppDirName}/resources/icons/icon.png" \
            "$out/share/icons/hicolor/512x512/apps/${pname}.png"
        fi

        install -Dm644 \
          ${linuxDesktopItem}/share/applications/${pname}.desktop \
          "$out/share/applications/${pname}.desktop"

        runHook postInstall
      '';

  doInstallCheck = true;
  installCheckPhase =
    if stdenv.hostPlatform.isDarwin then
      ''
        runHook preInstallCheck

        for path in \
          "$out/Applications/${appBundleName}" \
          "$out/Applications/${appBundleName}/Contents/MacOS/${appExecutableName}" \
          "$out/bin/${pname}"
        do
          if [ ! -e "$path" ]; then
            echo "missing required runtime path: $path" >&2
            exit 1
          fi
        done

        runHook postInstallCheck
      ''
    else
      ''
        runHook preInstallCheck

        for path in \
          "$out/lib/${linuxAppDirName}" \
          "$out/bin/${pname}" \
          "$out/share/applications/${pname}.desktop"
        do
          if [ ! -e "$path" ]; then
            echo "missing required runtime path: $path" >&2
            exit 1
          fi
        done

        runHook postInstallCheck
      '';

  passthru = {
    inherit
      appId
      appName
      appProtocolName
      appProtocolScheme
      electronDist
      electronRuntime
      electronRuntimeVersion
      electronVersion
      node_modules
      opencodeChannel
      ;

    macApp = {
      bundleName = appBundleName;
      bundleRelPath = "Applications/${appBundleName}";
      installMode = "copy";
    };
  };

  meta = with lib; {
    description = packageDescription;
    homepage = "https://github.com/anomalyco/opencode";
    license = licenses.mit;
    mainProgram = pname;
    platforms = [
      "aarch64-darwin"
      "x86_64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ];
  };
}
