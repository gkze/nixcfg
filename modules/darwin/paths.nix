{
  config,
  lib,
  primaryUser,
  ...
}:
let
  inherit (lib)
    attrByPath
    concatStringsSep
    escapeShellArg
    filter
    hasPrefix
    hasSuffix
    mkOption
    removePrefix
    types
    unique
    ;

  cfg = config.nixcfg.paths;

  # User entries stay owned by Home Manager's `nixcfg.paths.user`; this module
  # only composes them with the system directories, so the two layers never
  # define the same directory twice.
  userHome = attrByPath [
    "home-manager"
    "users"
    primaryUser
    "home"
    "homeDirectory"
  ] "/Users/${primaryUser}" config;
  userPaths = attrByPath [ "home-manager" "users" primaryUser "nixcfg" "paths" "user" ] [ ] config;

  expandHome =
    entry: if hasPrefix "$HOME" entry then "${userHome}${removePrefix "$HOME" entry}" else entry;

  composed = map expandHome (userPaths ++ cfg.system);
  composedPath = concatStringsSep ":" composed;

  malformed = filter (entry: !(hasPrefix "/" entry) || hasSuffix "/" entry) composed;
  repeated = filter (entry: lib.count (other: other == entry) composed > 1) (unique composed);
in
{
  options.nixcfg.paths.system = mkOption {
    type = types.listOf types.str;
    default = [
      "/opt/homebrew/bin"
      "/opt/homebrew/sbin"
      "/Users/${primaryUser}/.nix-profile/bin"
      "/etc/profiles/per-user/${primaryUser}/bin"
      "/run/current-system/sw/bin"
      "/nix/var/nix/profiles/default/bin"
      "/usr/local/bin"
      "/usr/bin"
      "/bin"
      "/usr/sbin"
      "/sbin"
    ];
    description = ''
      Ordered system PATH entries appended after `nixcfg.paths.user`.

      This list is the single source for both roots that do not inherit from
      each other: `environment.variables.PATH` (read by every zsh through
      `/etc/zshenv` and every bash login through `/etc/bashrc`) and
      `launchd.user.envVariables.PATH` (inherited by GUI-launched apps, hooks,
      and editor tasks). Nothing else may write to `PATH`, apart from
      tool-managed layers such as mise and direnv.
    '';
  };

  config = {
    assertions = [
      {
        assertion = repeated == [ ];
        message = "nixcfg.paths composed PATH repeats: ${builtins.toJSON repeated}";
      }
      {
        assertion = malformed == [ ];
        message = "nixcfg.paths composed PATH has malformed entries: ${builtins.toJSON malformed}";
      }
    ];

    environment.variables.PATH = concatStringsSep ":" composed;

    # `launchctl setenv` runs on every activation, so newly started GUI apps and
    # their children see the same base as a login shell.
    launchd.user.envVariables.PATH = composedPath;

    # Activation only runs on a switch, so the launchd user domain would fall
    # back to the system default after a logout or reboot. Re-apply the same
    # value at every login, matching the build-volume and locale agents.
    launchd.user.agents.nixcfg-path-env = {
      script = ''
        set -e
        /bin/launchctl setenv PATH ${escapeShellArg composedPath}
      '';
      serviceConfig = {
        Label = "dev.george.nixcfg-path-env";
        RunAtLoad = true;
      };
    };
  };
}
