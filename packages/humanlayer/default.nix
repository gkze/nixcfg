{
  lib,
  mkDmgApp,
  python3,
  selfSource,
  ...
}:
mkDmgApp {
  pname = "humanlayer";
  appName = "HumanLayer";
  sourceName = "Riptide-darwin-arm64.dmg";
  info = selfSource;

  # Skip generic fixups so only the reviewed updater patch and replacement
  # signature alter the app. The daemon remains nested in the vendor bundle.
  dontFixup = true;
  makeBinary = false;
  postInstallApp = ''
    app_bundle="$out/Applications/HumanLayer.app"
    main_executable="$app_bundle/Contents/MacOS/HumanLayer-Local"
    chmod -R u+w "$app_bundle"
    ${lib.getExe python3} ${./patch_updater.py} "$main_executable"
    /usr/bin/codesign \
      --force \
      --deep \
      --sign - \
      --preserve-metadata=identifier,entitlements,flags,runtime \
      "$app_bundle"
    ${lib.getExe python3} ${./patch_updater.py} --check "$main_executable"
    /usr/bin/codesign --verify --deep --strict "$app_bundle"

    mkdir -p "$out/bin"
    daemon="$app_bundle/Contents/Resources/bin/riptided"
    test -x "$daemon"
    ln -s "$daemon" "$out/bin/riptided"
  '';

  meta = with lib; {
    description = "AI coding IDE and agent collaboration platform";
    homepage = "https://www.humanlayer.com/";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "riptided";
  };
}
