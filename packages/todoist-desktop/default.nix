{
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "todoist-desktop";
  bundleName = "Todoist.app";
  executableName = "Todoist";
  info = selfSource;
  description = "Todoist desktop app for macOS";
  homepage = "https://todoist.com/";
  platforms = [ "aarch64-darwin" ];
}
