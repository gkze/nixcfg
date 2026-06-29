{
  mkDmgApp7zz,
  selfSource,
  system,
  ...
}:
mkDmgApp7zz {
  pname = "cleanshot";
  bundleName = "CleanShot X.app";
  executableName = "CleanShot X";
  sourceName = "CleanShot-X_${selfSource.version}_${system}.dmg";
  info = selfSource;
  description = "Screenshot and screen recording app for macOS";
  homepage = "https://cleanshot.com/";
}
