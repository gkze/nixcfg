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
  pname = "grok-build";
  appName = "Grok Build";
  bundleName = "Grok Build.app";
  executableName = "Grok";
  sourceAppPath = "Grok.app";
  info = selfSource;
  dontFixup = true;
  postInstallApp = ''
    app_bundle="$out/Applications/Grok Build.app"
    chmod -R u+w "$app_bundle"
    PYTHONPATH=${
      lib.fileset.toSource {
        root = ../..;
        fileset = lib.fileset.unions [
          ../../lib/__init__.py
          ../../lib/asar_integrity.py
        ];
      }
    } ${lib.getExe python3} ${./patch_renderer_ota.py} \
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
      verify = false;
    }}
  '';
  description = "xAI desktop workspace for building software with Grok";
  homepage = "https://grok.com/";
  platforms = [ "aarch64-darwin" ];
}
