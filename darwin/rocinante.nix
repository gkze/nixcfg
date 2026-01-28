{
  inputs,
  outputs,
  ...
}:
with outputs.lib;
with inputs;
mkDarwinHost {
  extraHomeModules = [
    (
      { config, ... }:
      {
        home.sessionVariables.OPENCODE_CONFIG = "${config.xdg.configHome}/opencode/personal.json";
      }
    )
  ];
  extraSystemModules = [
    "${modulesPath}/darwin/george/dock-apps.nix"
  ];
}
