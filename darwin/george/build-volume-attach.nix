{
  config,
  lib,
  pkgs,
  primaryUser,
  ...
}:
let
  volume = import ./build-volume-identity.nix;
  homeDirectory = config.home-manager.users.${primaryUser}.home.homeDirectory;
  bundlePath = "${homeDirectory}/${volume.bundle}";
  attach = pkgs.writeShellScript "build-volume-attach" ''
    set -eu
    buildVolumeBundle=${lib.escapeShellArg bundlePath}
    buildVolumeMountPoint=${lib.escapeShellArg volume.path}
    buildVolumeName=${lib.escapeShellArg volume.name}
    buildVolumeMaxSize=${lib.escapeShellArg volume.maxSize}
    ${builtins.readFile ./build-volume-attach.sh}
  '';
in
{
  # Login: launchd attaches the volume so it outlives any app or shell session.
  launchd.user.agents.build-volume.serviceConfig = {
    Label = "com.gkze.build-volume";
    ProgramArguments = [ "${attach}" ];
    RunAtLoad = true;
    StandardErrorPath = "${homeDirectory}/Library/Logs/build-volume.log";
  };

  # Activation: the agent above is loaded asynchronously, but Home Manager and
  # Homebrew run in this same activation and already point caches and bun at
  # the volume. Create and attach it synchronously, as the owning user, before
  # either consumer. Failure aborts the switch: continuing would let tools
  # create directories under the mount point on the root volume, which would
  # shadow the real mount on the next attach.
  system.activationScripts.preActivation.text = ''
    echo "attaching the Build volume..." >&2
    launchctl asuser "$(id -u -- ${lib.escapeShellArg primaryUser})" \
      sudo --user=${lib.escapeShellArg primaryUser} -- ${attach}
  '';
}
