{
  darwin,
  fetchurl,
  lib,
  selfSource,
  stdenvNoCC,
  system,
  unzip,
  ...
}:

stdenvNoCC.mkDerivation {
  pname = "macai";
  inherit (selfSource) version;

  passthru.macApp = {
    bundleName = "macai.app";
    bundleRelPath = "Applications/macai.app";
    installMode = "copy";
  };

  src = fetchurl {
    name = "macai-${selfSource.version}.zip";
    url = selfSource.urls.${system};
    hash = selfSource.hashes.${system};
  };

  nativeBuildInputs = [
    darwin.xattr
    unzip
  ];

  dontFixup = true;
  dontUnpack = true;

  installPhase = ''
    runHook preInstall

    unpack_dir="$TMPDIR/macai-unpack"
    mkdir -p "$unpack_dir" "$out/Applications" "$out/bin"
    unzip -qq "$src" -d "$unpack_dir"
    cp -R "$unpack_dir/macai.app" "$out/Applications/macai.app"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/macai.app"
    ln -s "$out/Applications/macai.app/Contents/MacOS/macai" "$out/bin/macai"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Native AI assistant for macOS";
    homepage = "https://renset.dev/macai/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "macai";
  };
}
