{
  inputs,
  outputs,
  ...
}:
with outputs.lib;
with inputs;
mkDarwinHost {
  extraHomeModules = [ { } ];
  extraSystemModules = [
    "${modulesPath}/darwin/george/dock-apps.nix"
  ];
}
