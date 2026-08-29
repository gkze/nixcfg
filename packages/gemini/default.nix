{
  mkSimpleDarwinApp,
  mkDmgApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkDmgApp;
  pname = "gemini";
  appName = "Gemini";
  sourceName = "Gemini.dmg";
  info = selfSource;
  dontFixup = true;
  postInstallApp = ''
    plist="$out/Applications/Gemini.app/Contents/Info.plist"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist")" = \
      "com.google.GeminiMacOS"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")" = \
      "${selfSource.version}"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$plist")" = \
      "${selfSource.version}"
  '';
  description = "Native Gemini assistant with screen context and macOS automation";
  homepage = "https://gemini.google/mac/";
  platforms = [ "aarch64-darwin" ];
}
