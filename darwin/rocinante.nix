{
  outputs,
  ...
}:
let
  inherit (outputs) lib;
in
lib.mkDarwinHost {
  user = "george";
  brewAppsModule = "${lib.modulesPath}/darwin/george/brew-apps.nix";
  extraHomeModules = [
    (_: {
      xdg.configFile."opencode/personal.json".text = builtins.toJSON {
        "$schema" = "https://opencode.ai/config.json";
        mcp = { };
      };
    })
  ];
  extraSystemModules = [
    "${lib.modulesPath}/darwin/george/dock-apps.nix"
    (lib.mkSetOpencodeEnvModule "personal.json")
  ];
}
