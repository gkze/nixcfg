{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "onepassword";
  appName = "1Password";
  bundleName = "1Password.app";
  executableName = "1Password";
  info = selfSource;
  description = "Multi-platform password manager";
  homepage = "https://1password.com/";
  platforms = [ "aarch64-darwin" ];
}
