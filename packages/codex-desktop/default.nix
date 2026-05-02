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
  appName = "Codex";
in
stdenvNoCC.mkDerivation {
  pname = "codex-desktop";
  inherit (selfSource) version;

  passthru.macApp = {
    bundleName = "${appName}.app";
    bundleRelPath = "Applications/${appName}.app";
    installMode = "symlink";
  };

  src = fetchurl {
    name = "${appName}_${selfSource.version}_${stdenvNoCC.hostPlatform.system}.zip";
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

    unpack_dir="$TMPDIR/codex-unpack"
    mkdir -p "$unpack_dir" "$out/Applications" "$out/bin"
    unzip -qq "$src" -d "$unpack_dir"

    app_bundle="$unpack_dir/${appName}.app"
    if [ ! -d "$app_bundle" ]; then
      echo "Expected ${appName}.app in Codex archive" >&2
      find "$unpack_dir" -maxdepth 2 -type d >&2
      exit 1
    fi

    cp -R "$app_bundle" "$out/Applications/${appName}.app"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/${appName}.app"
    ln -s "$out/Applications/${appName}.app/Contents/MacOS/${appName}" "$out/bin/codex-desktop"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Codex desktop app";
    homepage = "https://developers.openai.com/codex/app";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "codex-desktop";
  };
}
