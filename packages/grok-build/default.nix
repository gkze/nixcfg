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
    /usr/bin/codesign \
      --force \
      --deep \
      --sign - \
      --preserve-metadata=identifier,flags,runtime \
      --entitlements ${./Entitlements.plist} \
      "$app_bundle"
  '';
  description = "xAI desktop workspace for building software with Grok";
  homepage = "https://grok.com/";
  platforms = [ "aarch64-darwin" ];
}
