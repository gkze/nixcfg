{
  inputs,
  outputs,
  ...
}:
with outputs.lib;
with inputs;
mkDarwinHost {
  extraHomeModules = [
    (_: {
      xdg.configFile."opencode/personal.json".text = builtins.toJSON {
        "$schema" = "https://opencode.ai/config.json";
        mcp = { };
      };
    })
  ];
  extraSystemModules = [
    "${modulesPath}/darwin/george/dock-apps.nix"
    (
      { primaryUser, ... }:
      {
        launchd.user.envVariables.OPENCODE_CONFIG = "/Users/${primaryUser}/.config/opencode/personal.json";
      }
    )
  ];
}
