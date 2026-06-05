{
  lib,
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
  meta = with lib; {
    description = "Screenshot and screen recording app for macOS";
    homepage = "https://cleanshot.com/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "cleanshot";
  };
}
