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
    ${signAndVerifyBundle {
      bundle = ''"$app_bundle"'';
      preserveMetadata = [
        "identifier"
        "flags"
        "runtime"
      ];
      entitlements = ./Entitlements.plist;
    }}
  '';
  description = "AI workspace for projects, browser automation, and connected tools";
  homepage = "https://getenergy.com/";
  platforms = [ "aarch64-darwin" ];
}
