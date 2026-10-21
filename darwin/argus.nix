{ outputs, ... }:
let
  inherit (outputs) lib;
in
lib.mkDarwinHost {
  user = "george";
  # The default 6 GiB Rosetta Linux builder VM is too small for some of
  # the heavier x86_64-linux Rust builds we run from argus. The setting
  # is gated on the builder being enabled so an explicit no-builder caller
  # does not trip over an undeclared option.
  rosettaBuilderMemory = "16GiB";
  rosettaBuilderLingerMinutes = 30;
  brewAppsModule = "${lib.modulesPath}/darwin/george/brew-apps.nix";
  extraHomeModules = [
    ../home/george/work.nix
    "${lib.modulesPath}/home/darwin-closure-priority.nix"
    "${lib.modulesPath}/darwin/george/town-dock-apps.nix"
    (
      { pkgs, ... }:
      {
        # Install a few large host-specific tools on argus without opting this
        # host into the full
        # heavyOptional package set.
        nixcfg.packageSets.extraPackages = [
          pkgs.goose-cli
          pkgs.gws
        ];
      }
    )
  ];
  extraSystemModules = [
    "${lib.modulesPath}/darwin/george/caches.nix"
    "${lib.modulesPath}/darwin/george/work.nix"
    (
      { lib, ... }:
      {
        # Tailscale fallback for paths rocinante already built. The short name
        # resolves through MagicDNS. Unsigned paths are accepted only from this
        # pinned host. Mass query stays off so an unreachable rocinante does not
        # stall cache hits.
        nixcfg.common.nix.substituters = lib.mkBefore [
          "ssh-ng://george@rocinante?trusted=true&want-mass-query=false&max-connections=4&ssh-key=%2FUsers%2Fgeorge%2F.ssh%2Frocinante-nix-substituter&base64-ssh-public-host-key=AAAAC3NzaC1lZDI1NTE5AAAAIBeJLuJMiS2O9ZdBzDWRpZQqMt%2BSyH8ku6tLuvVvp2fW"
        ];
      }
    )
    (
      _:
      {
        # Second aarch64-darwin build machine. Rocinante shares argus's system
        # but has more RAM, so darwin builds can spread over both hosts instead
        # of queueing locally. The daemon connects as root, so root's
        # known_hosts must pin rocinante's host key; the sshKey below is the
        # same restricted-purpose key the substituter above already uses.
        nix.buildMachines = [
          {
            hostName = "rocinante";
            protocol = "ssh-ng";
            sshUser = "george";
            sshKey = "/Users/george/.ssh/rocinante-nix-substituter";
            systems = [ "aarch64-darwin" ];
            maxJobs = 4;
            speedFactor = 1;
          }
        ];
      }
    )
    {
      # Home Manager initializes completion after adding plugin directories to fpath.
      darwinDefaults.zsh.deferCompletionInitToHomeManager = true;
      # Preserve any pre-existing app/editor settings the first time Home Manager
      # takes over files like ~/.gemini/settings.json and VS Code Insiders
      # settings.json.
      home-manager.backupFileExtension = "backup";
    }
  ];
}
