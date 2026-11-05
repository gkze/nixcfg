# NixOS module for nix-rosetta-builder's VM image
# (`nix-rosetta-builder.potentiallyInsecureExtraNixosModule`).
#
# Evaluation cost is the reason for both definitions below. Neither changes how
# the VM behaves: the only content change is an unused channel copy that the
# image no longer carries.
{ inputs }:
{
  # Reuse the plain aarch64-linux Nixpkgs set. nix-rosetta-builder already
  # instantiates it (its nixpkgs follows ours), so with no overlays the image
  # configuration and its `qemu-efi` variant share that instance instead of
  # each importing Nixpkgs again on every Darwin host.
  nixpkgs.pkgs = inputs.nixpkgs.legacyPackages.aarch64-linux;

  # Mirrors the `system.build.image` call in nixpkgs'
  # nixos/modules/virtualisation/disk-image.nix with two deltas. Re-check it
  # against that file when nixpkgs changes the call.
  image.modules.qemu-efi =
    {
      config,
      lib,
      pkgs,
      modulesPath,
      ...
    }:
    {
      system.build.image = lib.mkForce (
        lib.overrideDerivation
          (import "${modulesPath}/../lib/make-disk-image.nix" {
            inherit lib config pkgs;
            inherit (config.virtualisation) diskSize;
            inherit (config.image) baseName format;
            partitionTableType = if config.image.efiSupport then "efi" else "legacy";
            # disk-image.nix always copies a filtered Nixpkgs tree into the
            # image as a channel. The builder disables channels, so the copy
            # is unused, and producing it walks and hashes all of Nixpkgs on
            # every evaluation.
            copyChannel = config.nix.channel.enable;
          })
          # Nix's VM command already falls back from KVM to TCG. Permit that
          # fallback when generating the image on hosted ARM runners without
          # KVM. This changes build scheduling only; the complete image still
          # boots in QEMU.
          (
            attrs: {
              requiredSystemFeatures = builtins.filter (feature: feature != "kvm") attrs.requiredSystemFeatures;
            }
          )
      );
    };
}
