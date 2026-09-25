# Nix's VM command already falls back from KVM to TCG. Permit that fallback
# when generating the builder disk image on hosted ARM runners without KVM.
# This changes build scheduling only; the complete image still boots in QEMU.
{ lib }:
_final: prev: {
  vmTools = prev.vmTools // {
    runInLinuxVM =
      drv:
      lib.overrideDerivation (prev.vmTools.runInLinuxVM drv) (attrs: {
        requiredSystemFeatures = builtins.filter (feature: feature != "kvm") attrs.requiredSystemFeatures;
      });
  };
}
