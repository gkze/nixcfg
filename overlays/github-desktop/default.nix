{
  inputs,
  prev,
  selfSource,
  slib,
  ...
}:
let
  inherit (selfSource) version;
  electronZipName = "electron-v${prev.electron.version}-${prev.stdenv.hostPlatform.node.platform}-${prev.stdenv.hostPlatform.node.arch}.zip";
  src = inputs.github-desktop;
  srcRev = inputs.github-desktop.rev;
in
{
  github-desktop = prev.github-desktop.overrideAttrs (
    finalAttrs: oldAttrs: {
      inherit src version;

      buildInputs =
        if prev.stdenv.hostPlatform.isLinux then
          oldAttrs.buildInputs
        else
          [
            prev.curl
          ];

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
                  cp -R ${prev.electron}/Applications/Electron.app Electron.app
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
        prev.lib.replaceStrings
          [
            "cp -r dist/*/resources $out/share/github-desktop"
            "ln -s $out/share/github-desktop/resources/app/static/icon-logo.png $out/share/icons/hicolor/512x512/apps/github-desktop.png"
          ]
          [
            ''
              packaged_resources=(dist/*/*.app/Contents/Resources)
              if [ -d "''${packaged_resources[0]}" ]; then
                cp -r "''${packaged_resources[0]}" "$out/share/github-desktop/resources"
              else
                cp -r dist/*/resources "$out/share/github-desktop"
              fi''
            "install -Dm444 app/static/linux/icon-logo.png $out/share/icons/hicolor/512x512/apps/github-desktop.png"
          ]
          oldAttrs.installPhase;

      preBuild = ''
        export CIRCLE_SHA1="${srcRev}"
      '';

      passthru = (oldAttrs.passthru or { }) // {
        upstreamChannel = "beta";
      };
    }
  );
}
