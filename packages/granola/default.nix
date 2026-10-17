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

  # The fixup phase rewrites the shebang in Contents/Resources/managed-update/
  # install-granola.sh to a Nix store path, which invalidates the bundle's
  # sealed resources. macOS Gatekeeper then refuses the app and launchd
  # SIGKILLs it on launch. Keep the extracted bundle byte-exact.
  dontFixup = true;

  # Fail the build instead of the launch if the sealed resources ever drift.
  postInstallApp = ''
    app_bundle="$out/Applications/Granola.app"
    /usr/bin/codesign --verify --deep --strict "$app_bundle"
  '';

  description = "AI meeting notes app for macOS";
  homepage = "https://granola.ai/";
}
