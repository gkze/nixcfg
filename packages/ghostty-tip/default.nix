{
  mkDmgApp7zz,
  selfSource,
  lib,
  ...
}:
mkDmgApp7zz {
  pname = "ghostty-tip";
  bundleName = "Ghostty.app";
  executableName = "ghostty";
  info = selfSource;
  createBin = true;
  meta = with lib; {
    description = "Nightly tip build of the Ghostty terminal emulator";
    homepage = "https://ghostty.org/";
    license = licenses.mit;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "ghostty";
  };
}
