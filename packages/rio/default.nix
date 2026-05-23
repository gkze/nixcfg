{
  mkDmgApp7zz,
  selfSource,
  lib,
  ...
}:
mkDmgApp7zz {
  pname = "rio";
  bundleName = "rio.app";
  executableName = "rio";
  info = selfSource;
  createBin = true;
  meta = with lib; {
    description = "Hardware-accelerated GPU terminal emulator powered by WebGPU";
    homepage = "https://github.com/raphamorim/rio/";
    license = licenses.mit;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "rio";
  };
}
