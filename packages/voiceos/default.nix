{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "voiceos";
  appName = "VoiceOS";
  info = selfSource;
  dontFixup = true;
  description = "Voice-driven desktop assistant for dictation and cross-app actions";
  homepage = "https://www.voiceos.com/";
  platforms = [
    "aarch64-darwin"
    "x86_64-darwin"
  ];
}
