{ lib, src }:
let
  inherit (import ./lib.nix) assertEq;

  plainLinuxPkgs = {
    fixture = "plain aarch64-linux Nixpkgs";
  };
  module = import (src + "/lib/rosetta-builder-image.nix") {
    inputs.nixpkgs.legacyPackages.aarch64-linux = plainLinuxPkgs;
  };

  # The module locates make-disk-image.nix relative to `modulesPath`, exactly
  # as nixpkgs' own disk-image module does, so the fixture tree mirrors
  # nixpkgs' nixos/{modules,lib} layout.
  imageFor =
    channelEnabled:
    let
      definition =
        (module.image.modules.qemu-efi {
          inherit lib;
          pkgs = {
            fixture = "image pkgs";
          };
          modulesPath = "${./rosetta-builder-image}/modules";
          config = {
            nix.channel.enable = channelEnabled;
            virtualisation.diskSize = 8192;
            image = {
              baseName = "nixos-image";
              efiSupport = true;
              format = "qcow2";
            };
          };
        }).system.build.image;
    in
    assert assertEq "image definition overrides the nixpkgs disk-image module"
      (lib.mkForce null).priority
      (definition.priority or null);
    definition.content;

  withoutChannel = imageFor false;
  withChannel = imageFor true;

  checks = [
    (assertEq "image configuration reuses the plain aarch64-linux set" plainLinuxPkgs
      module.nixpkgs.pkgs
    )
    (assertEq "image builder receives the disk-image module's arguments" {
      baseName = "nixos-image";
      copyChannel = false;
      diskSize = 8192;
      format = "qcow2";
      partitionTableType = "efi";
    } withoutChannel.recordedArgs)
    (assertEq "channel copy follows nix.channel.enable" true withChannel.recordedArgs.copyChannel)
    (assertEq "image drops only the kvm system feature" [
      "benchmark"
    ] withoutChannel.requiredSystemFeatures)
    (assertEq "image keeps the passthru nix-rosetta-builder reads" "nixos.qcow2"
      withoutChannel.passthru.filePath
    )
  ];
in
builtins.deepSeq checks true
