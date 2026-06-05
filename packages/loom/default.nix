{
  lib,
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
  meta = with lib; {
    description = "Screen recording and video messaging app";
    homepage = "https://www.loom.com/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "loom";
  };
}
