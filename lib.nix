{
  inputs,
  lib,
  ...
}@args:
let
  base = import ./lib/lib.nix args;
  toList =
    value:
    if value == null then
      [ ]
    else if builtins.isList value then
      value
    else
      [ value ];
in
base
// rec {
  mkSetOpencodeEnvModule =
    configName:
    { primaryUser, ... }:
    {
      launchd.user.agents.set-opencode-env = {
        script = ''
          launchctl setenv OPENCODE_CONFIG "/Users/${primaryUser}/.config/opencode/${configName}"
        '';
        serviceConfig = {
          Label = "com.nixcfg.set-opencode-env";
          RunAtLoad = true;
        };
      };
    };

  mkDarwinHost =
    {
      user,
      system ? "aarch64-darwin",
      work ? false,
      brewAppsModule ? null,
      extraHomeModules ? [ ],
      homeModulesByUser ? { },
      homeModuleArgsByUser ? { },
      includeDefaultUserModule ? true,
      extraSpecialArgs ? { },
      homeManagerExtraSpecialArgs ? { },
      extraSystemModules ? [ ],
      enableRosettaBuilder ? builtins.getEnv "CI" == "",
    }:
    base.mkSystem {
      inherit
        extraSpecialArgs
        homeManagerExtraSpecialArgs
        homeModuleArgsByUser
        homeModulesByUser
        system
        ;
      includeDefaultUserModules = includeDefaultUserModule;
      users = [ user ];
      homeModules =
        toList extraHomeModules ++ lib.lists.optionals work [ (_: { profiles.work.enable = true; }) ];
      systemModules = [
        inputs.nix-homebrew.darwinModules.nix-homebrew
        "${base.modulesPath}/darwin/homebrew.nix"
        { nixcfg.darwin.homebrew.user = lib.mkDefault user; }
      ]
      ++ lib.lists.optionals (brewAppsModule != null) [ brewAppsModule ]
      ++ lib.lists.optionals enableRosettaBuilder [
        inputs.nix-rosetta-builder.darwinModules.default
        { nix-rosetta-builder.onDemand = true; }
      ]
      ++ lib.lists.optionals work [ { profiles.work.enable = true; } ]
      ++ toList extraSystemModules;
    };
}
