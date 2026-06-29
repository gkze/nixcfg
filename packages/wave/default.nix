{
  mkDmgApp7zz,
  selfSource,
  lib,
  ...
}:
mkDmgApp7zz {
  pname = "wave";
  bundleName = "Wave.app";
  executableName = "Wave";
  info = selfSource;
  createBin = true;
  description = "Open-source terminal for seamless workflows";
  homepage = "https://www.waveterm.dev/";
  license = lib.licenses.asl20;
  platforms = [ "aarch64-darwin" ];
}
