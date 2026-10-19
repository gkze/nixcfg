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
  pname = "energy";
  appName = "Energy";
  info = selfSource;
  postInstallApp = ''
    app_bundle="$out/Applications/Energy.app"
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
      --preserve-metadata=identifier,flags,runtime \
      --entitlements ${./Entitlements.plist} \
      "$app_bundle"
    /usr/bin/codesign --verify --deep --strict "$app_bundle"
  '';
  description = "AI workspace for projects, browser automation, and connected tools";
  homepage = "https://getenergy.com/";
  platforms = [ "aarch64-darwin" ];
}
