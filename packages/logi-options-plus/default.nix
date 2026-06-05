{
  lib,
  mkZipApp,
  selfSource,
  ...
}:
mkZipApp {
  pname = "logi-options-plus";
  bundleName = "Logi Options+ Installer.app";
  executableName = "logioptionsplus_installer";
  binaryName = "logi-options-plus-installer";
  sourceAppPath = "logioptionsplus_installer.app";
  dontFixup = true;
  info = selfSource;
  meta = with lib; {
    description = "Installer for Logitech Options+";
    homepage = "https://www.logitech.com/en-us/software/logi-options-plus.html";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "logi-options-plus-installer";
  };
}
