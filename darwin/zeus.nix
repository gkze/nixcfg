{ outputs, ... }:
let
  profiles = import ./george/profiles.nix { slib = outputs.lib; };
in
profiles.mkHost [
  profiles.georgeBase
  profiles.townWorkstation
  {
    extraSystemModules = [
      { networking.computerName = "Zeus"; }
      # Determinate Nix owns the installation on this host: it writes
      # /etc/nix/nix.conf, runs determinate-nixd, creates the build users, and
      # schedules garbage collection. Letting nix-darwin manage Nix here as
      # well would mean two daemons and two writers for the same configuration,
      # so nix-darwin manages only the rest of the system. Determinate reads
      # /etc/nix/nix.custom.conf, which is where the cache, trust, and
      # store-headroom policy reaches the running daemon.
      (
        { config, lib, ... }:
        let
          nixConf = import ../lib/nix-conf.nix { inherit lib; };
          settings = config.nix.settings;
          common = config.nixcfg.common.nix;
        in
        {
          nix.enable = false;
          # nix-darwin refuses to schedule its GC and optimisation agents
          # unless it owns Nix. Determinate Nixd owns garbage collection on
          # this host, so the daily schedule and the `--delete-older-than 3d`
          # retention in `darwin/george/caches.nix` no longer reach it;
          # Determinate's own retention applies instead. The min-free/max-free
          # headroom below still bounds the store. The weekly dedupe job becomes
          # `auto-optimise-store` below so the store is still deduplicated.
          nix.gc.automatic = lib.mkForce false;
          nix.optimise.automatic = lib.mkForce false;
          environment.etc."nix/nix.custom.conf".text = nixConf.render {
            # The `extra-` forms append to whatever Determinate's own nix.conf
            # already sets, so cache.nixos.org, Determinate's substituter, the
            # root trust entry, and its experimental features all survive. That
            # also keeps the result independent of where Determinate includes
            # this file relative to the settings it generates.
            extra-substituters = common.substituters;
            extra-trusted-public-keys = common.trustedPublicKeys;
            extra-experimental-features = settings.experimental-features or null;
            extra-trusted-users = settings.trusted-users or [ ];
            keep-derivations = settings.keep-derivations or null;
            keep-outputs = settings.keep-outputs or null;
            min-free = settings.min-free or null;
            max-free = settings.max-free or null;
            # nix-darwin contributes these two on the hosts where it owns Nix.
            sandbox = false;
            sandbox-fallback = false;
            # Replaces the `nix.optimise` agent that needs `nix.enable`.
            auto-optimise-store = true;
          };
        }
      )
    ];
  }
]
