{ lib, ... }:
let
  env = import ./build-volume-env.nix;
in
{
  # GUI apps and launchd agents never read the shell's Home Manager session
  # variables; give them the same Build volume environment immediately and at
  # every later login, not only during the activation that adopts these values.
  launchd.user.envVariables = env;
  launchd.user.agents.build-volume-env = {
    # A middle setenv failure must not exit successfully with a partial
    # environment after later commands succeed.
    script = ''
      set -e
      ${lib.concatStringsSep "\n" (
        lib.mapAttrsToList (
          name: value: "/bin/launchctl setenv ${lib.escapeShellArg name} ${lib.escapeShellArg value}"
        ) env
      )}
    '';
    serviceConfig = {
      Label = "com.gkze.build-volume-env";
      RunAtLoad = true;
    };
  };
}
