{
  inputs,
  outputs,
  ...
}:
with outputs.lib;
with inputs;
mkDarwinHost {
  extraHomeModules = [
    "${modulesPath}/home/town.nix"
    (
      { config, ... }:
      {
        home.sessionVariables.OPENCODE_CONFIG = "${config.xdg.configHome}/opencode/work.json";
      }
    )
  ];
  extraSystemModules = [
    "${modulesPath}/darwin/town.nix"
    "${modulesPath}/darwin/george/town-dock-apps.nix"
  ];
}
