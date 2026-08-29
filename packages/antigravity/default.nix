{
  lib,
  mkDmgApp7zz,
  python3,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "antigravity";
  bundleName = "Antigravity.app";
  executableName = "Antigravity";
  info = selfSource;
  createBin = true;
  postInstallApp = ''
    app_bundle="$out/Applications/Antigravity.app"
    chmod -R u+w "$app_bundle"
    PYTHONPATH=${
      lib.fileset.toSource {
        root = ../..;
        fileset = lib.fileset.unions [
          ../../lib/__init__.py
          ../../lib/asar_integrity.py
        ];
      }
    } ${lib.getExe python3} ${./patch_updater.py} "$app_bundle"
    ${lib.getExe python3} ${./resign_bundle.py} "$app_bundle"
    PYTHONPATH=${
      lib.fileset.toSource {
        root = ../..;
        fileset = lib.fileset.unions [
          ../../lib/__init__.py
          ../../lib/asar_integrity.py
        ];
      }
    } ${lib.getExe python3} ${./patch_updater.py} --check "$app_bundle"
    ${lib.getExe python3} ${./resign_bundle.py} --check "$app_bundle"
  '';
  description = "Agentic development platform from Google";
  homepage = "https://antigravity.google/product/antigravity-2";
  platforms = [ "aarch64-darwin" ];
}
