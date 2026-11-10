{ outputs, ... }:
let
  profiles = import ./george/profiles.nix { slib = outputs.lib; };
in
# Rocinante is disabled while it is offline, so argus builds on its own slots:
# no static build machine and no rocinante substituter, so an unreachable host
# cannot fail machine selection or stall cache queries. Per-invocation offload
# still works because georgeBase trusts george, e.g.
# `nh darwin switch . -- -j 8 --builders 'ssh-ng://...'`.
#
# To offload to rocinante again, add `nix.buildMachines`, the
# `ssh-ng://george@rocinante` substituter, and `nix.settings.max-jobs = 0`
# here, and pin the host key with
# `ssh-keyscan rocinante >> /var/root/.ssh/known_hosts`.
profiles.mkHost [
  profiles.georgeBase
  profiles.townWorkstation
]
