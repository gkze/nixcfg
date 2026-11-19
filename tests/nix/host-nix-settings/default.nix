{ self }:
let
  inherit (import ../lib.nix) assertEq effectiveNixSettings;

  # Every George host must apply the same daemon policy, whether nix-darwin
  # writes /etc/nix/nix.conf itself or Determinate Nix owns the installation and
  # the host renders /etc/nix/nix.custom.conf instead. Checking the effective
  # settings per host keeps a host that stops owning Nix from silently losing
  # builder trust, cache access, or GC-root policy.
  hostChecks =
    hostName:
    let
      system = self.darwinConfigurations.${hostName}.config;
      settings = effectiveNixSettings system;
      common = system.nixcfg.common.nix;
      values = name: settings.${name} or [ ];
      missing =
        name: configured: builtins.filter (value: !(builtins.elem value (values name))) configured;
    in
    [
      (assertEq "${hostName} trusts george for per-invocation builders" true (
        builtins.elem "george" (values "trusted-users")
      ))
      (assertEq "${hostName} reaches every configured substituter" [ ] (
        missing "substituters" common.substituters
      ))
      (assertEq "${hostName} trusts every configured cache key" [ ] (
        missing "trusted-public-keys" common.trustedPublicKeys
      ))
      (assertEq "${hostName} keeps derivation GC roots off" [ "false" ] (values "keep-derivations"))
      (assertEq "${hostName} keeps output GC roots off" [ "false" ] (values "keep-outputs"))
    ]
    ++ (
      if common.minFreeStoreBytes == null then
        [ ]
      else
        [
          (assertEq "${hostName} starts GC at the configured headroom" [
            (toString common.minFreeStoreBytes)
          ] (values "min-free"))
          (assertEq "${hostName} targets the configured headroom" [
            (toString common.maxFreeStoreBytes)
          ] (values "max-free"))
        ]
    );
in
builtins.deepSeq (map hostChecks (builtins.attrNames self.darwinConfigurations)) true
