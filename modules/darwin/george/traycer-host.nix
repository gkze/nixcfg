# Argus-only activation guard; keep it outside darwin/ host auto-discovery.
{
  config,
  lib,
  primaryUser,
  ...
}:
{
  system.activationScripts.applications.text = lib.mkBefore ''
    /bin/bash ${./traycer-host-collision-preflight.sh} \
      ${lib.escapeShellArg (toString config.users.users.${primaryUser}.uid)} \
      ${lib.escapeShellArg "/Users/${primaryUser}"} \
      ${lib.escapeShellArg "/Applications"} \
      /bin/launchctl \
      /usr/bin/plutil
  '';
}
