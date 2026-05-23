{
  mkDmgApp7zz,
  selfSource,
  lib,
  ...
}:
mkDmgApp7zz {
  pname = "antigravity";
  bundleName = "Antigravity.app";
  executableName = "Antigravity";
  info = selfSource;
  createBin = true;
  meta = with lib; {
    description = "Agentic development platform from Google";
    homepage = "https://antigravity.google/product/antigravity-2";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "antigravity";
  };
}
