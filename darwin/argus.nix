{ outputs, ... }:
let
  profiles = import ./george/profiles.nix { slib = outputs.lib; };
in
profiles.mkHost [
  profiles.georgeBase
  profiles.townWorkstation
  {
    extraSystemModules = [
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
      {
        # `builders` is a restricted setting: the daemon ignores a
        # client-supplied one unless the connecting user is trusted. Listing
        # george here is what makes per-invocation builders work, e.g.
        # `nh darwin switch . -- --builders 'ssh-ng://george@rocinante ...'`.
        # No machine is configured statically, so no host depends on rocinante
        # being reachable.
        nix.settings.trusted-users = [ "george" ];
      }
    ];
  }
]
