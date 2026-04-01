{
  stdenvNoCC,
  fetchurl,
  selfSource,
  lib,
  ...
}:
let
  appName = "Commander";
in
stdenvNoCC.mkDerivation {
  pname = "commander";
  inherit (selfSource) version;

  src = fetchurl {
    url = selfSource.urls.${stdenvNoCC.hostPlatform.system};
    hash = selfSource.hashes.${stdenvNoCC.hostPlatform.system};
  };

  dontUnpack = true;

  installPhase = ''
    runHook preInstall

    mount_dir="$TMPDIR/${appName}-mount"
    mkdir -p "$mount_dir" "$out/Applications" "$out/bin"

    cleanup() {
      /usr/bin/hdiutil detach "$mount_dir" -quiet >/dev/null 2>&1 || true
    }
    trap cleanup EXIT

    /usr/bin/hdiutil attach "$src" \
      -nobrowse \
      -readonly \
      -mountpoint "$mount_dir" \
      -quiet

    cp -a "$mount_dir/${appName}.app" "$out/Applications/"
    /usr/bin/xattr -cr "$out/Applications/${appName}.app"
    ln -s "$out/Applications/${appName}.app/Contents/MacOS/${appName}" "$out/bin/commander"

    cleanup
    trap - EXIT

    runHook postInstall
  '';

  meta = with lib; {
    description = "Native macOS workspace for AI coding agents";
    homepage = "https://thecommander.app/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "commander";
  };
}
