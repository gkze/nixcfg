{
  lib,
  mkDmgApp,
  mkSimpleDarwinApp,
  python3,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkDmgApp;
  pname = "github-copilot-app";
  appName = "GitHub Copilot";
  executableName = "github";
  info = selfSource;
  description = "Desktop workspace for agent-driven development with GitHub Copilot";
  homepage = "https://github.com/features/ai/github-app";
  dontFixup = true;
  postInstallApp = ''
    app_bundle="$out/Applications/GitHub Copilot.app"
    main_executable="$app_bundle/Contents/MacOS/github"
    chmod -R u+w "$app_bundle"
    ${lib.getExe python3} ${./patch_updater.py} "$main_executable"
    /usr/bin/codesign \
      --force \
      --deep \
      --sign - \
      --preserve-metadata=identifier,entitlements,flags,runtime \
      "$app_bundle"
    ${lib.getExe python3} ${./patch_updater.py} --check "$main_executable"
    /usr/bin/codesign --verify --deep --strict "$app_bundle"
  '';
  platforms = [ "aarch64-darwin" ];
}
