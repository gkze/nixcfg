{ outputs, ... }:
let
  profiles = import ./george/profiles.nix { slib = outputs.lib; };
in
profiles.mkHost [
  profiles.georgeBase
  profiles.townWorkstation
  {
    extraHomeModules = [ ./george/build-volume.nix ];
    extraSystemModules = [
      {
        # Rocinante is disabled while it is offline, so argus builds on its own
        # slots again: no static build machine and no rocinante substituter, so
        # an unreachable host cannot fail machine selection or stall cache
        # queries. Per-invocation offload still works because `builders` is a
        # restricted setting and george is trusted, e.g.
        # `nh darwin switch . -- -j 8 --builders 'ssh-ng://...'`.
        #
        # To offload to rocinante again, restore `nix.buildMachines`, the
        # `ssh-ng://george@rocinante` substituter, and `max-jobs = 0`, and pin
        # the host key with
        # `ssh-keyscan rocinante >> /var/root/.ssh/known_hosts`.
        nix.settings = {
          # `builders` is a restricted setting: the daemon ignores a
          # client-supplied one unless the connecting user is trusted. Listing
          # george here is what makes per-invocation builders work, e.g.
          # `nh darwin switch . -- --builders 'ssh-ng://...'`.
          trusted-users = [ "george" ];
          max-jobs = "auto";
        };
      }
      ./argus/spotlight.nix
      ./george/build-volume-launchd.nix
    ];
  }
]
