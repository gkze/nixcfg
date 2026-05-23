{
  darwin,
  fetchurl,
  lib,
  selfSource,
  stdenvNoCC,
  system,
  ...
}:

stdenvNoCC.mkDerivation {
  pname = "nordvpn";
  inherit (selfSource) version;

  passthru.macApp = {
    bundleName = "NordVPN.app";
    bundleRelPath = "Applications/NordVPN.app";
    installMode = "copy";
  };

  src = fetchurl {
    url = selfSource.urls.${system};
    hash = selfSource.hashes.${system};
  };

  nativeBuildInputs = [ darwin.xattr ];

  dontFixup = true;
  dontUnpack = true;

  installPhase = ''
    runHook preInstall

    pkg_dir="$TMPDIR/nordvpn-pkg"
    mkdir -p "$out/Applications" "$out/bin"
    /usr/sbin/pkgutil --expand-full "$src" "$pkg_dir"
    app_bundle="$(find "$pkg_dir" -maxdepth 4 -type d -name "NordVPN.app" -print -quit)"
    if [ -z "$app_bundle" ]; then
      echo "Expected NordVPN.app in NordVPN pkg" >&2
      find "$pkg_dir" -maxdepth 4 -type d -name "*.app" >&2
      exit 1
    fi
    cp -R "$app_bundle" "$out/Applications/NordVPN.app"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/NordVPN.app"
    ln -s "$out/Applications/NordVPN.app/Contents/MacOS/NordVPN" "$out/bin/nordvpn"

    runHook postInstall
  '';

  meta = with lib; {
    description = "NordVPN client for macOS";
    homepage = "https://nordvpn.com/";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "nordvpn";
  };
}
