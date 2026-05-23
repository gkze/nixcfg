{
  mkDmgApp7zz,
  selfSource,
  lib,
  ...
}:
mkDmgApp7zz {
  pname = "spotify";
  bundleName = "Spotify.app";
  executableName = "Spotify";
  info = selfSource;
  createBin = true;
  meta = with lib; {
    description = "Music client for Spotify";
    homepage = "https://www.spotify.com/";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "spotify";
  };
}
