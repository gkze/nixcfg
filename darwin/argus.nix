{
  inputs,
  outputs,
  ...
}:
with outputs.lib;
with inputs;
mkDarwinHost {
  user = "george";
  work = true;
  brewAppsModule = "${modulesPath}/darwin/george/brew-apps.nix";
  extraSystemModules = [
    "${modulesPath}/darwin/george/town-dock-apps.nix"
    (mkSetOpencodeEnvModule "work.json")
  ];
}
