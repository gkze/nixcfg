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
  ];
  extraSystemModules = [
    "${modulesPath}/darwin/town.nix"
    "${modulesPath}/darwin/george/town-dock-apps.nix"
    (mkSetOpencodeEnvModule "work.json")
  ];
}
