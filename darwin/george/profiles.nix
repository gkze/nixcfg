# George's Darwin host profiles. Each host file composes profiles with
# `mkHost` and lists only its own differences. This directory has no
# default.nix, so flakelight does not treat it as a host.
{ slib }:
let
  moduleListArgs = [
    "extraHomeModules"
    "extraSystemModules"
  ];

  # Later profiles override scalar arguments and append to module lists.
  combine =
    left: right:
    left
    // right
    // builtins.listToAttrs (
      map (name: {
        inherit name;
        value = (left.${name} or [ ]) ++ (right.${name} or [ ]);
      }) moduleListArgs
    );
in
rec {
  # Shared by every machine George uses.
  georgeBase = {
    user = "george";
    rosettaBuilderLingerMinutes = 30;
    brewAppsModule = ./brew-apps.nix;
    extraHomeModules = [
      "${slib.modulesPath}/home/darwin-closure-priority.nix"
    ];
    extraSystemModules = [
      ./caches.nix
      ./locale-launchd.nix
      {
        # Home Manager initializes completion after adding plugin directories to fpath.
        darwinDefaults.zsh.deferCompletionInitToHomeManager = true;
        # George's current preference; set "automatic" to follow the time of day again.
        darwinDefaults.appearance = "dark";
        # Preserve any pre-existing app/editor settings the first time Home Manager
        # takes over files like ~/.gemini/settings.json and VS Code Insiders
        # settings.json.
        home-manager.backupFileExtension = "backup";
      }
    ];
  };

  # Town workstations: the work profile, Town Dock, and Town tooling.
  townWorkstation = {
    # The default 6 GiB Rosetta Linux builder VM is too small for some of
    # the heavier x86_64-linux Rust builds we run from Town workstations.
    rosettaBuilderMemory = "16GiB";
    extraHomeModules = [
      ../../home/george/work.nix
      ./town-dock-apps.nix
      (
        { pkgs, ... }:
        {
          # Install a few large Town tools without opting the host into the
          # full heavyOptional package set.
          nixcfg.packageSets.extraPackages = [
            pkgs.goose-cli
            pkgs.gws
          ];
        }
      )
    ];
    extraSystemModules = [ ./work.nix ];
  };

  mkHost = profiles: slib.mkDarwinHost (builtins.foldl' combine { } profiles);
}
