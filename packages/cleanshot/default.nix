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

stdenvNoCC.mkDerivation {
  pname = "cleanshot";
  inherit (selfSource) version;

  passthru.macApp = {
    bundleName = "CleanShot X.app";
    bundleRelPath = "Applications/CleanShot X.app";
    installMode = "copy";
  };

  src = fetchurl {
    name = "CleanShot-X_${selfSource.version}_${system}.dmg";
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

    mkdir -p "$out/Applications" "$out/bin"
    7zz x -sns- -y "$src" "CleanShot X.app" "CleanShot X.app/*" -o"$out/Applications"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/CleanShot X.app"
    ln -s "$out/Applications/CleanShot X.app/Contents/MacOS/CleanShot X" "$out/bin/cleanshot"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Screenshot and screen recording app for macOS";
    homepage = "https://cleanshot.com/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "cleanshot";
  };
}
