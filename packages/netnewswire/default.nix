{
  stdenvNoCC,
  fetchurl,
  selfSource,
  lib,
  unzip,
  darwin,
  ...
}:
let
  appName = "NetNewsWire";
in
stdenvNoCC.mkDerivation {
  pname = "netnewswire";
  inherit (selfSource) version;

  passthru.macApp = {
    bundleName = "${appName}.app";
    bundleRelPath = "Applications/${appName}.app";
    installMode = "copy";
  };

  src = fetchurl {
    url = selfSource.urls.${stdenvNoCC.hostPlatform.system};
    hash = selfSource.hashes.${stdenvNoCC.hostPlatform.system};
  };

  dontUnpack = true;
  nativeBuildInputs = [
    unzip
    darwin.xattr
  ];

  installPhase = ''
    runHook preInstall

    unpack_dir="$TMPDIR/netnewswire-unpack"
    mkdir -p "$unpack_dir" "$out/Applications" "$out/bin"
    unzip -qq "$src" -d "$unpack_dir"

    app_bundle="$unpack_dir/${appName}.app"
    if [ ! -d "$app_bundle" ]; then
      echo "Expected ${appName}.app in NetNewsWire archive" >&2
      exit 1
    fi

    cp -R "$app_bundle" "$out/Applications/${appName}.app"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/${appName}.app"
    ln -s "$out/Applications/${appName}.app/Contents/MacOS/${appName}" "$out/bin/netnewswire"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Free and open source RSS reader for macOS";
    homepage = "https://netnewswire.com/";
    license = licenses.mit;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "netnewswire";
  };
}
