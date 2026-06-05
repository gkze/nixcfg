{
  lib,
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "pica";
  bundleName = "Pica.app";
  info = selfSource;
  meta = {
    description = "Native macOS color picker";
    homepage = "https://pica.joshpuckett.me/";
    license = lib.licenses.unfree;
    mainProgram = "pica";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
}
