{ outputs, ... }:
let
  inherit (outputs) lib;
in
lib.mkDarwinHost {
  user = "george";
  brewAppsModule = "${lib.modulesPath}/darwin/george/brew-apps.nix";
  extraHomeModules = [
    "${lib.modulesPath}/home/darwin-closure-priority.nix"
    "${lib.modulesPath}/darwin/george/dock-apps.nix"
  ];
  extraSystemModules = [
    "${lib.modulesPath}/darwin/george/caches.nix"
    {
      # Preserve any pre-existing app/editor settings the first time Home Manager
      # takes over files like ~/.gemini/settings.json and VS Code Insiders
      # settings.json.
      home-manager.backupFileExtension = "backup";
    }
    (lib.mkSetOpencodeEnvModule "personal.json")
  ];
}
