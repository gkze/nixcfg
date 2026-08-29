{
  mkDmgApp,
  mkSimpleDarwinApp,
  selfSource,
  ...
}:
# Tembo has no supported updater-disable flag. Keep the system-owned copy
# read-only instead of modifying and re-signing the vendor bundle here.
mkSimpleDarwinApp {
  builder = mkDmgApp;
  pname = "tembo";
  appName = "Tembo";
  info = selfSource;
  dontFixup = true;
  description = "Desktop client for running and reviewing Tembo coding agents";
  homepage = "https://www.tembo.io/download";
  platforms = [
    "aarch64-darwin"
    "x86_64-darwin"
  ];
}
