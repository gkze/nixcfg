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
  pname = "airfoil";
  inherit (selfSource) version;

  passthru.macApp = {
    bundleName = "Airfoil.app";
    bundleRelPath = "Applications/Airfoil.app";
    installMode = "copy";
  };

  src = fetchurl {
    url = selfSource.urls.${system};
    hash = selfSource.hashes.${system};
  };

  dontFixup = true;

  nativeBuildInputs = [
    darwin.xattr
    unzip
  ];

  dontUnpack = true;

  installPhase = ''
    runHook preInstall

    unpack_dir="$TMPDIR/airfoil-unpack"
    mkdir -p "$unpack_dir" "$out/Applications" "$out/bin"
    unzip -qq "$src" -d "$unpack_dir"
    cp -R "$unpack_dir/Airfoil/Airfoil.app" "$out/Applications/Airfoil.app"
    cp -R "$unpack_dir/Airfoil/Airfoil Satellite.app" "$out/Applications/Airfoil Satellite.app"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/Airfoil.app"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/Airfoil Satellite.app"
    ln -s "$out/Applications/Airfoil.app/Contents/MacOS/Airfoil" "$out/bin/airfoil"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Send audio from your Mac to compatible receivers";
    homepage = "https://rogueamoeba.com/airfoil/mac/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "airfoil";
  };
}
