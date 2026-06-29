{
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "pica";
  bundleName = "Pica.app";
  info = selfSource;
  description = "Native macOS color picker";
  homepage = "https://pica.joshpuckett.me/";
  platforms = [ "aarch64-darwin" ];
}
