{
  lib,
  mkSimpleDarwinApp,
  mkZipApp,
  python3,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "zo";
  appName = "Zo";
  info = selfSource;
  dontFixup = true;
  postInstallApp = ''
    app_bundle="$out/Applications/Zo.app"
    chmod -R u+w "$app_bundle"
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
  '';
  description = "Desktop client for the Zo personal cloud computer";
  homepage = "https://zo.computer/";
  platforms = [
    "aarch64-darwin"
    "x86_64-darwin"
  ];
}
