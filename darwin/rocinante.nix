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
        xdg.configFile."opencode/personal.json".text = builtins.toJSON {
          "$schema" = "https://opencode.ai/config.json";
          mcp = { };
        };
      }
    )
  ];
  extraSystemModules = [
    "${modulesPath}/darwin/george/dock-apps.nix"
  ];
}
