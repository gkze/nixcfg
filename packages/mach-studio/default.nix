{
  lib,
  mkSimpleDarwinApp,
  mkDmgApp,
  python3,
  selfSource,
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
    /usr/bin/codesign \
      --force \
      --deep \
      --sign - \
      --preserve-metadata=identifier,entitlements,flags,runtime \
      "$app_bundle"
    /usr/bin/codesign --verify --deep --strict "$app_bundle"
  '';
  description = "Local AI workspace for running open models on Apple Silicon";
  homepage = "https://withsyzygy.com/product";
  platforms = [ "aarch64-darwin" ];
}
