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
        # Build work belongs on rocinante. It is argus's second aarch64-darwin
        # host and has twice the RAM, so closure and update builds run there
        # instead of competing with interactive work here. nix-darwin renders
        # `publicHostKey` as a bare machines-file field that Nix 2.35 rejects,
        # so the daemon verifies rocinante through root's known_hosts; that host
        # key is pinned once during bootstrap with
        # `ssh-keyscan rocinante >> /var/root/.ssh/known_hosts`.
        nix.buildMachines = [
          {
            hostName = "rocinante";
            protocol = "ssh-ng";
            sshUser = "george";
            sshKey = "/Users/george/.ssh/rocinante-nix-substituter";
            systems = [ "aarch64-darwin" ];
            maxJobs = 8;
            # Rocinante wins machine selection whenever local slots exist, so
            # `max-jobs` can be raised again without losing the preference.
            speedFactor = 2;
            # Rocinante is the only aarch64-darwin builder, so it advertises the
            # feature classes argus could otherwise satisfy on its own.
            supportedFeatures = [
              "benchmark"
              "big-parallel"
            ];
          }
        ];
        # No local build slots: every derivation belongs to rocinante or the
        # Linux builder, so argus stays free for interactive work. An
        # unreachable rocinante fails a build loudly instead of silently
        # rebuilding here; `nix build -j <n>` restores local slots for one
        # invocation. Substitutions are unaffected either way.
        nix.settings = {
          # `builders` is a restricted setting: the daemon ignores a
          # client-supplied one unless the connecting user is trusted. Listing
          # george here is what makes per-invocation builders work, e.g.
          # `nh darwin switch . -- --builders 'ssh-ng://...'`.
          trusted-users = [ "george" ];
          max-jobs = 0;
        };
      }
      {
        darwinDefaults.spotlight.indexing = false;
      }
    ];
  }
]
