{ ... }: {
  # imports = [ (modulesPath + "/installer/scan/not-detected.nix") ];

  # boot.initrd.availableKernelModules = [ "xhci_pci" "thunderbolt" "nvme" "usb_storage" "sd_mod" ];
  # boot.initrd.kernelModules = [ ];
  # boot.kernelModules = [ "kvm-intel" ];
  # boot.extraModulePackages = [ ];

  # fileSystems."/" =
  #   {
  #     device = "/dev/disk/by-uuid/3900d3b0-334b-4e0e-aafe-f46c4dd13a3f";
  #     fsType = "ext4";
  #   };

  # boot.initrd.luks.devices."luks-ea2de727-3163-4f8c-874a-1cfd9e537a8c".device = "/dev/disk/by-uuid/ea2de727-3163-4f8c-874a-1cfd9e537a8c";

  # fileSystems."/boot" =
  #   {
  #     device = "/dev/disk/by-uuid/F54B-D0C3";
  #     fsType = "vfat";
  #   };

  # swapDevices =
  #   [{ device = "/dev/disk/by-uuid/34822754-f52c-4daa-b5f9-26b5d1229a6f"; }];

  # # Enables DHCP on each ethernet and wireless interface. In case of scripted networking
  # # (the default) this is the recommended approach. When using systemd-networkd it's
  # # still possible to use this option, but it's recommended to use it in conjunction
  # # with explicit per-interface declarations with `networking.interfaces.<interface>.useDHCP`.
  # networking.useDHCP = lib.mkDefault true;
  # # networking.interfaces.wlp0s20f3.useDHCP = lib.mkDefault true;

  # nixpkgs.hostPlatform = lib.mkDefault "x86_64-linux";
  # powerManagement.cpuFreqGovernor = lib.mkDefault "powersave";
  # hardware.cpu.intel.updateMicrocode = lib.mkDefault config.hardware.enableRedistributableFirmware;
}
