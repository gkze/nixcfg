{ config, pkgs, lib, modulesPath, ... }: {
  imports = [ (modulesPath + "/installer/scan/not-detected.nix") ];

  boot = {
    # Inter-Integrated Circuit (I2C)
    # https://en.wikipedia.org/wiki/I%C2%B2C
    # Used for communicating with external monitor(s) over DDC (Display Data
    # Channel)
    # Currently used to set brightness
    kernelModules = [ "i2c-dev" "kvm-intel" ];
    initrd = {
      availableKernelModules = [
        "nvme"
        "sd_mod"
        "thunderbolt"
        "usb_storage"
        "xhci_pci"
      ];
      luks.devices = {
        "luks-a3c480ba-744e-48f7-b509-fb525972d806".device = "/dev/disk/by-uuid/a3c480ba-744e-48f7-b509-fb525972d806";
        "luks-5283c0a3-818c-4e5e-aad5-b46b75bc677f".device = "/dev/disk/by-uuid/5283c0a3-818c-4e5e-aad5-b46b75bc677f";
      };
    };
    loader = { efi.canTouchEfiVariables = true; systemd-boot.enable = true; };
  };

  systemd.services.fprintd.after = [ "display-manager.service" ];

  services = {
    udev.extraRules = ''
      KERNEL=="i2c-[0-9]*", GROUP="i2c", MODE="0660"
    '';
    fprintd = {
      enable = true;
      package = pkgs.fprintd-tod;
      tod = { enable = true; driver = pkgs.libfprint-2-tod1-vfs0090; };
    };
  };

  fileSystems = {
    "/" = {
      device = "/dev/disk/by-uuid/c2e89227-23c9-4d05-926d-e0414c693801";
      fsType = "ext4";
    };
    "/boot" = {
      device = "/dev/disk/by-uuid/AFEC-5C32";
      fsType = "vfat";
    };
  };

  swapDevices = [
    { device = "/dev/disk/by-uuid/082a8004-f741-4862-8b9e-273f00ff520d"; }
  ];

  networking.useDHCP = lib.mkDefault true;

  powerManagement.cpuFreqGovernor = lib.mkDefault "powersave";
  hardware.cpu.intel.updateMicrocode = lib.mkDefault config.hardware.enableRedistributableFirmware;
}
