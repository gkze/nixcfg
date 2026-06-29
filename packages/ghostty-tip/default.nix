{
  mkDmgApp7zz,
  selfSource,
  lib,
  ...
}:
mkDmgApp7zz {
  pname = "ghostty-tip";
  bundleName = "Ghostty.app";
  executableName = "ghostty";
  info = selfSource;
  createBin = true;
  description = "Nightly tip build of the Ghostty terminal emulator";
  homepage = "https://ghostty.org/";
  license = lib.licenses.mit;
  mainProgram = "ghostty";
  platforms = [ "aarch64-darwin" ];
}
