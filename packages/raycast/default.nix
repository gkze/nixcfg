{
  mkDmgApp,
  selfSource,
  lib,
  ...
}:
mkDmgApp {
  pname = "raycast";
  info = selfSource;
  meta = with lib; {
    description = "Productivity launcher and command palette for macOS";
    homepage = "https://www.raycast.com/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "raycast";
  };
}
