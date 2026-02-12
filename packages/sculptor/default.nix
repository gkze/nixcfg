{
  mkDmgApp,
  outputs,
  stdenv,
  appimageTools,
  fetchurl,
  lib,
  ...
}:
let
  inherit (outputs.lib) sources;
  info = sources.sculptor;
  inherit (stdenv.hostPlatform) system;
  inherit (stdenv) isDarwin;
  meta = with lib; {
    description = "UI for running parallel coding agents in safe, isolated sandboxes";
    homepage = "https://imbue.com/sculptor/";
    license = licenses.unfree;
    platforms = [
      "aarch64-darwin"
      "x86_64-linux"
    ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "sculptor";
  };
in
if isDarwin then
  mkDmgApp {
    pname = "sculptor";
    inherit info meta;
  }
else
  let
    src = fetchurl {
      name = "Sculptor_${info.version}.AppImage";
      url = info.urls.${system};
      hash = info.hashes.${system};
    };
  in
  appimageTools.wrapType2 {
    pname = "sculptor";
    inherit (info) version;
    inherit meta src;

    extraInstallCommands =
      let
        appimageContents = appimageTools.extractType2 {
          inherit (info) version;
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
