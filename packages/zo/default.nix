{
  lib,
  mkSimpleDarwinApp,
  mkZipApp,
  python3,
  selfSource,
  signAndVerifyBundle,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "zo";
  appName = "Zo";
  info = selfSource;
  dontFixup = true;
  verifyAppSignature = true;
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
    ${signAndVerifyBundle {
      bundle = ''"$app_bundle"'';
      preserveMetadata = [
        "identifier"
        "flags"
        "runtime"
      ];
      entitlements = ./Entitlements.plist;
      verify = true;
    }}
  '';
  description = "Desktop client for the Zo personal cloud computer";
  homepage = "https://zo.computer/";
  platforms = [
    "aarch64-darwin"
    "x86_64-darwin"
  ];
}
