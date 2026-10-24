{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "granola";
  appName = "Granola";
  info = selfSource;

  # Fail the build instead of the launch if the sealed resources ever drift.
  postInstallApp = ''
    app_bundle="$out/Applications/Granola.app"
    /usr/bin/codesign --verify --deep --strict "$app_bundle"
  '';

  description = "AI meeting notes app for macOS";
  homepage = "https://granola.ai/";
}
