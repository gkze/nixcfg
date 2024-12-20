{
  config,
  pkgs,
  lib,
  modulesPath,
  inputs,
  ...
}:
{
  imports = [
    (modulesPath + "/installer/scan/not-detected.nix")
    inputs.nixos-hardware.nixosModules.lenovo-thinkpad-x1-10th-gen
  ];

  boot = {
    binfmt.registrations.appimage = {
      wrapInterpreterInShell = false;
      interpreter = "${pkgs.appimage-run}/bin/appimage-run";
      recognitionType = "magic";
      offset = 0;
      mask = "\\xff\\xff\\xff\\xff\\x00\\x00\\x00\\x00\\xff\\xff\\xff";
      magicOrExtension = "\\x7fELF....AI\\x02";
    };
    # Inter-Integrated Circuit (I2C)
    # https://en.wikipedia.org/wiki/I%C2%B2C
    # Used for communicating with external monitor(s) over DDC (Display Data
    # Channel)
    # Currently used to set brightness
    consoleLogLevel = 0;
    kernelModules = [
      "i2c-dev"
      "kvm-intel"
    ];
    kernelParams = [
      "i915.force_probe=46a6"
      "quiet"
      "splash"
      "boot.shell_on_fail"
      "loglevel=3"
      "rd.systemd.show_status=false"
      "rd.udev.log_level=3"
      "udev.log_priority=3"
    ];
    initrd = {
      availableKernelModules = [
        "nvme"
        "sd_mod"
        "thunderbolt"
        "usb_storage"
        "xhci_pci"
      ];
      luks.devices = {
        "luks-5283c0a3-818c-4e5e-aad5-b46b75bc677f".device = "/dev/nvme0n1p2";
        "luks-a3c480ba-744e-48f7-b509-fb525972d806".device = "/dev/nvme0n1p3";
      };
      verbose = false;
    };
    loader = {
      efi.canTouchEfiVariables = true;
      systemd-boot.enable = true;
      timeout = 0;
    };
    tmp = {
      useTmpfs = true;
      cleanOnBoot = true;
    };
    plymouth = {
      enable = true;
      theme = "lone";
      themePackages = with pkgs; [
        # By default we would install all themes
        (adi1090x-plymouth-themes.override {
          selected_themes = [ "lone" ];
        })
      ];
    };
  };

  systemd.services.fprintd.after = [ "display-manager.service" ];

  services = {
    # auto-cpufreq = {
    #   enable = true;
    #   settings = {
    #     battery = { governor = "powersave"; turbo = "never"; };
    #     charger = { governor = "performance"; turbo = "auto"; };
    #   };
    # };
    udev.extraRules = ''
      KERNEL=="i2c-[0-9]*", GROUP="i2c", MODE="0660"
      ACTION=="remove",\
       ENV{ID_BUS}=="usb",\
       ENV{ID_MODEL_ID}=="0407",\
       ENV{ID_VENDOR_ID}=="1050",\
       ENV{ID_VENDOR}=="Yubico",\
       RUN+="${pkgs.systemd}/bin/loginctl lock-sessions"
    '';
    fprintd = {
      enable = true;
      package = pkgs.fprintd-tod;
      tod = {
        enable = true;
        driver = pkgs.libfprint-2-tod1-vfs0090;
      };
    };
    thermald.enable = true;
    xserver.videoDrivers = [ "modesetting" ];
  };

  fileSystems = {
    "/" = {
      device = "/dev/dm-0";
      fsType = "ext4";
    };
    "/boot" = {
      device = "/dev/nvme0n1p1";
      fsType = "vfat";
    };
  };

  swapDevices = [ { device = "/dev/dm-1"; } ];

  networking.useDHCP = lib.mkDefault true;

  # powerManagement.cpuFreqGovernor = lib.mkDefault "powersave";
  powerManagement.powertop.enable = true;
  # TODO: determine if intel graphics custom config is needed or automatic
  hardware = {
    cpu.intel.updateMicrocode = lib.mkDefault config.hardware.enableRedistributableFirmware;
    graphics = {
      enable = true;
      extraPackages = with pkgs; [
        intel-media-driver
        vpl-gpu-rt
      ];
    };
  };
  environment.sessionVariables = {
    LIBVA_DRIVER_NAME = "iHD";
  };
}
