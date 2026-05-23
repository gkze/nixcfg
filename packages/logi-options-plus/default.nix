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
  pname = "logi-options-plus";
  inherit (selfSource) version;

  passthru.macApp = {
    bundleName = "Logi Options+ Installer.app";
    bundleRelPath = "Applications/Logi Options+ Installer.app";
    installMode = "copy";
  };

  src = fetchurl {
    url = selfSource.urls.${system};
    hash = selfSource.hashes.${system};
  };

  nativeBuildInputs = [
    darwin.xattr
    unzip
  ];

  dontFixup = true;
  dontUnpack = true;

  installPhase = ''
    runHook preInstall

    unpack_dir="$TMPDIR/logi-options-plus-unpack"
    mkdir -p "$unpack_dir" "$out/Applications" "$out/bin"
    unzip -qq "$src" -d "$unpack_dir"
    cp -R "$unpack_dir/logioptionsplus_installer.app" "$out/Applications/Logi Options+ Installer.app"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/Logi Options+ Installer.app"
    ln -s "$out/Applications/Logi Options+ Installer.app/Contents/MacOS/logioptionsplus_installer" "$out/bin/logi-options-plus-installer"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Installer for Logitech Options+";
    homepage = "https://www.logitech.com/en-us/software/logi-options-plus.html";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "logi-options-plus-installer";
  };
}
