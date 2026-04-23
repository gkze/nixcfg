{
  mkDmgApp,
  selfSource,
  stdenv,
  appimageTools,
  fetchurl,
  lib,
  ...
}:
let
  inherit (stdenv.hostPlatform) system;
  inherit (stdenv) isDarwin;
  meta = with lib; {
    description = "UI for running parallel coding agents in safe, isolated sandboxes";
    homepage = "https://imbue.com/sculptor/";
    license = licenses.unfree;
    platforms = [
      "aarch64-darwin"
      "x86_64-darwin"
      "x86_64-linux"
    ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "sculptor";
  };
in
if isDarwin then
  mkDmgApp {
    pname = "sculptor";
    info = selfSource;
    inherit meta;
  }
else
  let
    src = fetchurl {
      name = "Sculptor_${selfSource.version}.AppImage";
      url = selfSource.urls.${system};
      hash = selfSource.hashes.${system};
    };
  in
  appimageTools.wrapType2 {
    pname = "sculptor";
    inherit (selfSource) version;
    inherit meta src;

    extraInstallCommands =
      let
        appimageContents = appimageTools.extractType2 {
          inherit (selfSource) version;
          inherit src;
          pname = "sculptor";
        };
      in
      ''
        # Install desktop file and icons if available
        if [ -d "${appimageContents}/usr/share" ]; then
          cp -r "${appimageContents}/usr/share" "$out/"
        fi
      '';
  }
