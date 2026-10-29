{ lib, ... }:
let
  env.LANG = "en_US.UTF-8";
in
{
  # GUI apps start without LANG, so shells they spawn (such as the Claude
  # desktop Terminal panel) run zsh in the C locale, which measures prompts in
  # bytes and corrupts wrapped line redraws. Terminal emulators that set LANG
  # themselves keep their value; this only fills in what launchd hands out.
  # nix-darwin applies envVariables during activation only, so the agent
  # reapplies them at every later login.
  launchd.user.envVariables = env;
  launchd.user.agents.locale-env = {
    script = ''
      set -e
      ${lib.concatStringsSep "\n" (
        lib.mapAttrsToList (
          name: value: "/bin/launchctl setenv ${lib.escapeShellArg name} ${lib.escapeShellArg value}"
        ) env
      )}
    '';
    serviceConfig = {
      Label = "com.gkze.locale-env";
      RunAtLoad = true;
    };
  };
}
