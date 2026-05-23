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
  pname = "loom";
  inherit (selfSource) version;

  passthru.macApp = {
    bundleName = "Loom.app";
    bundleRelPath = "Applications/Loom.app";
    installMode = "copy";
  };

  src = fetchurl {
    name = "Loom_${selfSource.version}_${system}.dmg";
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
    7zz x -sns- -y "$src" "Loom.app" "Loom.app/*" -o"$out/Applications"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/Loom.app"
    ln -s "$out/Applications/Loom.app/Contents/MacOS/Loom" "$out/bin/loom"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Screen recording and video messaging app";
    homepage = "https://www.loom.com/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "loom";
  };
}
