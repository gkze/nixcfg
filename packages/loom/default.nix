{
  mkDmgApp7zz,
  selfSource,
  system,
  ...
}:
mkDmgApp7zz {
  pname = "loom";
  bundleName = "Loom.app";
  executableName = "Loom";
  sourceName = "Loom_${selfSource.version}_${system}.dmg";
  info = selfSource;
  description = "Screen recording and video messaging app";
  homepage = "https://www.loom.com/";
}
