{
  mkDmgApp7zz,
  selfSource,
  lib,
  ...
}:
mkDmgApp7zz {
  pname = "warp-preview";
  bundleName = "WarpPreview.app";
  executableName = "preview";
  info = selfSource;
  createBin = true;
  meta = with lib; {
    description = "Preview channel of the Warp terminal";
    homepage = "https://www.warp.dev/";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "warp-preview";
  };
}
