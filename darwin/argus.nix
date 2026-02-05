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
    (
      { primaryUser, ... }:
      {
        launchd.user.agents.set-opencode-env = {
          script = ''
            launchctl setenv OPENCODE_CONFIG "/Users/${primaryUser}/.config/opencode/work.json"
          '';
          serviceConfig = {
            Label = "com.george.set-opencode-env";
            RunAtLoad = true;
          };
        };
      }
    )
  ];
}
