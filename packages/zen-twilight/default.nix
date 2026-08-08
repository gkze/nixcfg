{
  bash-dynamic-pipe-heredoc,
  mkDmgApp,
  selfSource,
  stdenvNoCC,
  lib,
  ...
}:
mkDmgApp {
  # Bash 5.3 can deadlock on pipe-backed heredocs after XNU reduces pipe
  # capacity. Keep the upstream backport local to this package instead of
  # invalidating nixpkgs' wider Darwin stdenv graph.
  stdenv = stdenvNoCC.override (old: {
    shell = "${bash-dynamic-pipe-heredoc}/bin/bash";
    initialPath = [ bash-dynamic-pipe-heredoc ] ++ old.initialPath;
    allowedRequisites = old.allowedRequisites ++ [ bash-dynamic-pipe-heredoc ];
    extraAttrs = old.extraAttrs // {
      shellPackage = bash-dynamic-pipe-heredoc;
    };
  });
  pname = "zen-twilight";
  appName = "twilight";
  executableName = "zen";
  info = selfSource;
  sourceName = "zen.macos-universal.dmg";
  codesignApp = true;
  # Keep Firefox's install hash stable across rebuilds by launching from
  # /Applications/Twilight.app instead of a changing Nix store path.
  macApp.installMode = "copy";
  postInstallApp = ''
    app="$out/Applications/Twilight.app"
    resources="$app/Contents/Resources"
    browser_resources="$resources/browser"

    expected_version=${lib.escapeShellArg selfSource.version}
    app_version=""
    build_id=""
    while IFS="=" read -r key value; do
      case "$key" in
        Version) app_version="$value" ;;
        BuildID) build_id="$value" ;;
      esac
    done < "$resources/application.ini"
    actual_version="$app_version-$build_id"
    if [[ "$actual_version" != "$expected_version" ]]; then
      echo >&2 "Twilight artifact version mismatch: expected $expected_version, got $actual_version"
      exit 1
    fi

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
