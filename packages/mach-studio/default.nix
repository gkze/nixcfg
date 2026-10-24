{
  lib,
  mkSimpleDarwinApp,
  mkDmgApp,
  python3,
  selfSource,
  signAndVerifyBundle,
  ...
}:
mkSimpleDarwinApp {
  builder = mkDmgApp;
  pname = "mach-studio";
  appName = "Mach Studio";
  info = selfSource;
  sourceName = "Mach-Studio-${selfSource.version}-arm64.dmg";
  postInstallApp = ''
    app_bundle="$out/Applications/Mach Studio.app"
    PYTHONPATH=${
      lib.fileset.toSource {
        root = ../..;
        fileset = lib.fileset.unions [
          ../../lib/__init__.py
          ../../lib/asar_integrity.py
        ];
      }
    } ${lib.getExe python3} ${./patch_updater.py} \
      "$app_bundle/Contents/Resources/app.asar" \
      "$app_bundle/Contents/Info.plist"
    ${signAndVerifyBundle {
      bundle = ''"$app_bundle"'';
      preserveMetadata = [
        "identifier"
        "entitlements"
        "flags"
        "runtime"
      ];
    }}
  '';
  description = "Local AI workspace for running open models on Apple Silicon";
  homepage = "https://withsyzygy.com/product";
  platforms = [ "aarch64-darwin" ];
}
