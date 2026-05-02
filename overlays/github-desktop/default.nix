{
  final,
  inputs,
  prev,
  selfSource,
  slib,
  ...
}:
let
  inherit (selfSource) version;
  electronRuntime = final.nixcfgElectron.runtimeFor "40.1.0";
  electronZipName = "electron-v${electronRuntime.version}-${prev.stdenv.hostPlatform.node.platform}-${prev.stdenv.hostPlatform.node.arch}.zip";
  src = inputs.github-desktop;
  srcRev = inputs.github-desktop.rev;
in
{
  github-desktop = (prev.github-desktop.override { electron = electronRuntime; }).overrideAttrs (
    finalAttrs: oldAttrs: {
      inherit src version;

      buildInputs =
        if prev.stdenv.hostPlatform.isLinux then
          oldAttrs.buildInputs
        else
          [
            prev.curl
          ];

      nativeBuildInputs =
        if prev.stdenv.hostPlatform.isDarwin then
          prev.lib.remove prev.desktopToDarwinBundle oldAttrs.nativeBuildInputs
        else
          oldAttrs.nativeBuildInputs;

      cacheRoot = prev.fetchYarnDeps {
        name = "${finalAttrs.pname}-cache-root";
        yarnLock = finalAttrs.src + "/yarn.lock";
        hash = slib.sourceHash "github-desktop" "yarnRootHash";
      };

      cacheApp = prev.fetchYarnDeps {
        name = "${finalAttrs.pname}-cache-app";
        yarnLock = finalAttrs.src + "/app/yarn.lock";
        hash = slib.sourceHash "github-desktop" "yarnAppHash";
      };

      postConfigure =
        prev.lib.replaceStrings
          [
            "yarn --cwd app/node_modules/desktop-notifications run install"
            ''
              touch electron
              zip -0Xqr ${electronZipName} electron
              rm electron''
          ]
          [
            ''
              while IFS= read -r node_addon_api_header; do
                if grep -Fq 'static const napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>(-1);' "$node_addon_api_header"; then
                  substituteInPlace "$node_addon_api_header" \
                    --replace-fail \
                      'static const napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>(-1);' \
                      'static const napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>(0);'
                fi
              done < <(find app/node_modules node_modules -path '*/node-addon-api/napi.h' -type f)

              yarn --cwd app/node_modules/desktop-notifications run install''
            (
              if prev.stdenv.hostPlatform.isDarwin then
                ''
                  cp -R ${electronRuntime.passthru.dist}/Electron.app Electron.app
                  chmod -R u+w Electron.app
                  zip -0Xqr ${electronZipName} Electron.app
                  rm -rf Electron.app''
              else
                ''
                  touch electron
                  zip -0Xqr ${electronZipName} electron
                  rm electron''
            )
          ]
          oldAttrs.postConfigure;

      installPhase =
        if prev.stdenv.hostPlatform.isDarwin then
          ''
            runHook preInstall

            shopt -s nullglob
            packaged_apps=(dist/*/*.app)
            if [ "''${#packaged_apps[@]}" -ne 1 ]; then
              printf 'expected exactly one packaged GitHub Desktop app, found %s\n' "''${#packaged_apps[@]}" >&2
              exit 1
            fi

            mkdir -p "$out/Applications" "$out/bin"
            cp -R "''${packaged_apps[0]}" "$out/Applications/GitHub Desktop.app"

            info_plist="$out/Applications/GitHub Desktop.app/Contents/Info.plist"
            ${prev.lib.getExe prev.python3} ${./validate_info_plist.py} "$info_plist"

            ln -s "$out/Applications/GitHub Desktop.app/Contents/MacOS/GitHub Desktop" "$out/bin/github-desktop"
            install -Dm444 app/static/linux/icon-logo.png "$out/share/icons/hicolor/512x512/apps/github-desktop.png"

            runHook postInstall
          ''
        else
          prev.lib.replaceStrings
            [
              "ln -s $out/share/github-desktop/resources/app/static/icon-logo.png $out/share/icons/hicolor/512x512/apps/github-desktop.png"
            ]
            [
              "install -Dm444 app/static/linux/icon-logo.png $out/share/icons/hicolor/512x512/apps/github-desktop.png"
            ]
            oldAttrs.installPhase;

      preBuild = ''
        export CIRCLE_SHA1="${srcRev}"
      '';

      passthru = (oldAttrs.passthru or { }) // {
        inherit electronRuntime;
        electronDist = electronRuntime.passthru.dist;
        electronHeaders = electronRuntime.passthru.headers;
        electronRuntimeVersion = electronRuntime.version;
        electronVersion = electronRuntime.version;
        upstreamChannel = "beta";
      };
    }
  );
}
