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
  inherit (stdenv.hostPlatform) isDarwin system;
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

    # The fixup phase rewrites shebangs in the bundled backend scripts to Nix
    # store paths. That invalidates Contents/_CodeSignature/CodeResources, so
    # macOS Gatekeeper refuses the bundle and launchd SIGKILLs it on launch. It
    # would also point scripts at /nix/store from inside the Docker containers
    # Sculptor copies them into. Keep the extracted bundle byte-exact.
    dontFixup = true;

    # Fail the build instead of the launch if the sealed resources ever drift.
    postInstallApp = ''
      app_bundle="$out/Applications/Sculptor.app"
      /usr/bin/codesign --verify --deep --strict "$app_bundle"
    '';
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
