{ outputs, ... }:
let
  inherit (outputs) lib;
in
lib.mkDarwinHost {
  user = "george";
  brewAppsModule = "${lib.modulesPath}/darwin/george/brew-apps.nix";
  extraHomeModules = [
    "${lib.modulesPath}/home/darwin-closure-priority.nix"
  ];
  extraSystemModules = [
    "${lib.modulesPath}/darwin/george/dock-apps.nix"
    (lib.mkSetOpencodeEnvModule "active.json")
  ];
}
