{
  lib,
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "todoist-desktop";
  bundleName = "Todoist.app";
  executableName = "Todoist";
  info = selfSource;
  meta = {
    description = "Todoist desktop app for macOS";
    homepage = "https://todoist.com/";
    license = lib.licenses.unfree;
    mainProgram = "todoist-desktop";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
}
