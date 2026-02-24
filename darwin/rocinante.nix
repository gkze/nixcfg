{
  inputs,
  outputs,
  ...
}:
with outputs.lib;
with inputs;
mkDarwinHost {
  user = "george";
  brewAppsModule = "${modulesPath}/darwin/george/brew-apps.nix";
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
    (mkSetOpencodeEnvModule "personal.json")
  ];
}
