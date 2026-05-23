{
  _7zz,
  darwin,
  fetchurl,
  lib,
  selfSource,
  stdenvNoCC,
  system,
  ...
}:
let
  packageArch =
    {
      aarch64-darwin = "arm64";
    }
    .${system};
in
stdenvNoCC.mkDerivation {
  pname = "google-drive";
  inherit (selfSource) version;

  passthru.macApp = {
    bundleName = "Google Drive.app";
    bundleRelPath = "Applications/Google Drive.app";
    installMode = "copy";
  };

  src = fetchurl {
    url = selfSource.urls.${system};
    hash = selfSource.hashes.${system};
  };

  nativeBuildInputs = [
    _7zz
    darwin.xattr
  ];

  dontFixup = true;
  dontUnpack = true;

  installPhase = ''
    runHook preInstall

    unpack_dir="$TMPDIR/google-drive-unpack"
    mkdir -p "$unpack_dir" "$out/Applications" "$out/bin"
    7zz x -sns- -snld -y "$src" -o"$unpack_dir"
    /usr/sbin/pkgutil --expand-full "$unpack_dir/Install Google Drive/GoogleDrive.pkg" "$unpack_dir/pkg"
    cp -R "$unpack_dir/pkg/GoogleDrive_${packageArch}.pkg/Payload/Google Drive.app" "$out/Applications/Google Drive.app"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/Google Drive.app"
    ln -s "$out/Applications/Google Drive.app/Contents/MacOS/Google Drive" "$out/bin/google-drive"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Google Drive sync client";
    homepage = "https://www.google.com/drive/";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "google-drive";
  };
}
