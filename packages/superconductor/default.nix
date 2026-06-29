{
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "superconductor";
  bundleName = "Superconductor.app";
  sourceAppPath = "super.engineering.app";
  executableName = "superconductor";
  info = selfSource;
  description = "Native macOS app for AI coding workflows";
  homepage = "https://superconductor.so/";
  platforms = [ "aarch64-darwin" ];
}
