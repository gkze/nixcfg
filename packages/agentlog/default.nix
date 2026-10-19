{
  mkDmgApp,
  mkSimpleDarwinApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkDmgApp;
  pname = "agentlog";
  appName = "Agentlog";
  executableName = "agentlog";
  info = selfSource;
  description = "Task board for coordinating AI coding agents";
  homepage = "https://agentlog.dev/";
  platforms = [ "aarch64-darwin" ];

  postInstallApp = ''
    app_bundle="$out/Applications/Agentlog.app"
    plist="$app_bundle/Contents/Info.plist"

    chmod -R u+w "$app_bundle"
    /usr/bin/plutil -replace CFBundleShortVersionString -string "${selfSource.version}" "$plist"
    /usr/bin/plutil -replace CFBundleVersion -string "${selfSource.version}" "$plist"

    /usr/bin/codesign \
      --force \
      --deep \
      --sign - \
      --preserve-metadata=identifier,entitlements,flags,runtime \
      "$app_bundle"

    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")" = \
      "${selfSource.version}"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$plist")" = \
      "${selfSource.version}"
    /usr/bin/codesign --verify --deep --strict "$app_bundle"
  '';
}
