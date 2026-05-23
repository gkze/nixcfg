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
  pname = "tailscale-app";
  inherit (selfSource) version;

  passthru.macApp = {
    bundleName = "Tailscale.app";
    bundleRelPath = "Applications/Tailscale.app";
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

    pkg_dir="$TMPDIR/tailscale-pkg"
    mkdir -p "$out/Applications/Tailscale.app" "$out/bin"
    /usr/sbin/pkgutil --expand-full "$src" "$pkg_dir"
    contents_dir="$(find "$pkg_dir" -maxdepth 4 -type d -path "*/Payload/Contents" -print -quit)"
    if [ -z "$contents_dir" ]; then
      echo "Expected Tailscale.app Contents payload in Tailscale pkg" >&2
      find "$pkg_dir" -maxdepth 4 -type d >&2
      exit 1
    fi
    cp -R "$contents_dir" "$out/Applications/Tailscale.app/Contents"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/Tailscale.app"
    ln -s "$out/Applications/Tailscale.app/Contents/MacOS/Tailscale" "$out/bin/tailscale"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Tailscale GUI client for macOS";
    homepage = "https://tailscale.com/";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "tailscale";
  };
}
