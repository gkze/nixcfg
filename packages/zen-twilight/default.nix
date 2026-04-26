{
  mkDmgApp,
  selfSource,
  lib,
  ...
}:
mkDmgApp {
  pname = "zen-twilight";
  appName = "twilight";
  executableName = "zen";
  info = selfSource;
  codesignApp = true;
  # Keep Firefox's install hash stable across rebuilds by launching from
  # /Applications/Twilight.app instead of a changing Nix store path.
  macApp.installMode = "copy";
  postInstallApp = ''
    app="$out/Applications/Twilight.app"
    resources="$app/Contents/Resources"
    browser_resources="$resources/browser"

    mkdir -p "$resources/defaults/pref"
    mkdir -p "$browser_resources/defaults/preferences"
    cp ${../../home/george/zen/autoconfig/autoconfig.js} "$resources/defaults/pref/autoconfig.js"
    cp ${../../home/george/zen/autoconfig/autoconfig.js} "$browser_resources/defaults/preferences/autoconfig.js"
    cp ${../../home/george/zen/autoconfig/twilight.cfg} "$resources/twilight.cfg"
    cp ${../../home/george/zen/autoconfig/twilight.cfg} "$browser_resources/twilight.cfg"
  '';
  meta = with lib; {
    description = "Twilight channel of the Zen Browser with nixcfg AutoConfig";
    homepage = "https://zen-browser.app/";
    license = licenses.mpl20;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "zen-twilight";
  };
}
