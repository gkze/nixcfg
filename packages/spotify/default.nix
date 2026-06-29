{
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "spotify";
  bundleName = "Spotify.app";
  executableName = "Spotify";
  info = selfSource;
  createBin = true;
  description = "Music client for Spotify";
  homepage = "https://www.spotify.com/";
  platforms = [ "aarch64-darwin" ];
}
