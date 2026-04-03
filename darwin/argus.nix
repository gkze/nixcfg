{ outputs, ... }:
let
  inherit (outputs) lib;
in
lib.mkDarwinHost {
  user = "george";
  work = true;
  brewAppsModule = "${lib.modulesPath}/darwin/george/brew-apps.nix";
  extraHomeModules = [
    "${lib.modulesPath}/home/darwin-closure-priority.nix"
  ];
  extraSystemModules = [
    "${lib.modulesPath}/darwin/george/town-dock-apps.nix"
    (lib.mkSetOpencodeEnvModule "active.json")
  ];
}
