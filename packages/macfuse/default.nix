{
  darwin,
  fetchurl,
  lib,
  selfSource,
  stdenvNoCC,
  system,
  undmg,
  ...
}:

stdenvNoCC.mkDerivation {
  pname = "macfuse";
  inherit (selfSource) version;

  src = fetchurl {
    url = selfSource.urls.${system};
    hash = selfSource.hashes.${system};
  };

  nativeBuildInputs = [
    darwin.xattr
    undmg
  ];

  dontFixup = true;
  sourceRoot = ".";

  unpackCmd = ''
    runHook preUnpack
    undmg "$src"
    runHook postUnpack
  '';

  installPhase = ''
    runHook preInstall

    pkg_dir="$TMPDIR/macfuse-pkg"
    /usr/sbin/pkgutil --expand-full "Extras/macFUSE ${selfSource.version}.pkg" "$pkg_dir"
    mkdir -p "$out"
    cp -R "$pkg_dir/Core.pkg/Payload/Library" "$out/Library"
    cp -R "$pkg_dir/Core.pkg/Payload/usr" "$out/usr"
    cp -R "$pkg_dir/PreferencePane.pkg/Payload/Library/PreferencePanes" "$out/Library/PreferencePanes"
    mkdir -p "$out/Applications"
    cp -R "Extras/Uninstaller.app" "$out/Applications/macFUSE Uninstaller.app"
    ${darwin.xattr}/bin/xattr -cr "$out"

    runHook postInstall
  '';

  meta = with lib; {
    description = "File system integration layer for macOS";
    homepage = "https://macfuse.github.io/";
    license = licenses.bsd3;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
  };
}
