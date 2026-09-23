{
  mkDmgApp7zz,
  selfSource,
  lib,
  ...
}:
mkDmgApp7zz {
  pname = "thorium";
  bundleName = "Thorium.app";
  executableName = "Thorium";
  # Chromium locates its framework relative to the executable's invocation path.
  createBin = false;
  info = selfSource;
  description = "Chromium-based browser with performance optimizations";
  homepage = "https://thorium.rocks/";
  license = lib.licenses.bsd3;
  platforms = [
    "aarch64-darwin"
    "x86_64-darwin"
  ];
}
