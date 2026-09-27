# nix-rosetta-builder ships `system.build.images.qemu-efi`. NixOS builds that
# image through virtualisation/disk-image.nix, which leaves make-disk-image's
# copyChannel at its default of true. That runs lib.cleanSource over the whole
# Nixpkgs tree in every Darwin host evaluation (~20s wall, ~8s sys per host) to
# embed a channel the builder never uses: its configuration sets
# `nix.channel.enable = false`.
#
# Keep these arguments in sync with virtualisation/disk-image.nix; remove this
# module once upstream derives copyChannel from the channel setting.
{
  config,
  lib,
  modulesPath,
  pkgs,
  ...
}:
{
  system.build.image = lib.mkForce (
    import (modulesPath + "/../lib/make-disk-image.nix") {
      inherit config lib pkgs;
      inherit (config.virtualisation) diskSize;
      inherit (config.image) baseName format;
      partitionTableType = if config.image.efiSupport then "efi" else "legacy";
      copyChannel = config.nix.channel.enable;
    }
  );
}
