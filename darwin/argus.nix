{
  inputs,
  outputs,
  ...
}:
with outputs.lib;
with inputs;
mkDarwinHost {
  work = true;
  extraSystemModules = [
    "${modulesPath}/darwin/george/town-dock-apps.nix"
    (mkSetOpencodeEnvModule "work.json")
  ];
}
