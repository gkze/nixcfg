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
  appName = "Granola";
in
stdenvNoCC.mkDerivation {
  pname = "granola";
  inherit (selfSource) version;

  passthru.macApp = {
    bundleName = "${appName}.app";
    bundleRelPath = "Applications/${appName}.app";
    installMode = "symlink";
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

    unpack_dir="$TMPDIR/granola-unpack"
    mkdir -p "$unpack_dir" "$out/Applications" "$out/bin"
    unzip -qq "$src" -d "$unpack_dir"

    app_bundle="$(echo "$unpack_dir"/*.app)"
    if [ ! -d "$app_bundle" ]; then
      echo "Expected ${appName}.app in Granola archive" >&2
      exit 1
    fi

    cp -R "$app_bundle" "$out/Applications/${appName}.app"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/${appName}.app"
    ln -s "$out/Applications/${appName}.app/Contents/MacOS/${appName}" "$out/bin/granola"

    runHook postInstall
  '';

  meta = with lib; {
    description = "AI meeting notes app for macOS";
    homepage = "https://granola.ai/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "granola";
  };
}
