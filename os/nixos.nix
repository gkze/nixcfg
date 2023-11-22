{ pkgs, lib, ... }:
let locale = "en_US.UTF-8"; in {
  imports = [ ./hardware-configuration.nix ];

  boot = {
    loader = { systemd-boot.enable = true; efi.canTouchEfiVariables = true; };
    initrd.luks.devices."luks-4c3acb5a-1b6c-4e4f-a29d-b9ed8dcc9682".device = "/dev/disk/by-uuid/4c3acb5a-1b6c-4e4f-a29d-b9ed8dcc9682";
  };

  networking = {
    hostName = "frontier"; # Define your hostname.
    networkmanager.enable = true;
  };

  hardware = {
    bluetooth = { enable = true; powerOnBoot = true; };
    pulseaudio.enable = false;
  };

  virtualisation.libvirtd.enable = true;

  sound.enable = true;

  i18n = {
    defaultLocale = locale;
    extraLocaleSettings = {
      LC_ADDRESS = locale;
      LC_IDENTIFICATION = locale;
      LC_MEASUREMENT = locale;
      LC_MONETARY = locale;
      LC_NAME = locale;
      LC_NUMERIC = locale;
      LC_PAPER = locale;
      LC_TELEPHONE = locale;
      LC_TIME = locale;
    };
  };

  fonts.fonts = with pkgs; [ (nerdfonts.override { fonts = [ "Hack" ]; }) ];

  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  nixpkgs.config.allowUnfree = true;

  services = {
    # Bluebooth
    blueman.enable = true;

    # FirmWare UPdate Daemon
    fwupd.enable = true;

    # X11 windowing system
    xserver = {
      enable = true;

      # GNOME desktop environment
      displayManager.gdm.enable = true;
      desktopManager.gnome.enable = true;

      # X11 keymap
      layout = "us";
      xkbVariant = "";
    };

    # Audio
    pipewire = {
      enable = true;
      alsa.enable = true;
      alsa.support32Bit = true;
      pulse.enable = true;
    };

    # Fingerprint reader (TOD = Touch OEM Driver)
    fprintd = {
      enable = true;
      package = pkgs.fprintd-tod;
      tod = {
        enable = true;
        driver = pkgs.libfprint-2-tod1-goodix;
      };
    };
  };

  programs = {
    dconf.enable = true;
    gnupg.agent = { enable = true; enableSSHSupport = true; };
  };

  security.rtkit.enable = true;

  # Define a user account. Don't forget to set a password with ‘passwd’.
  users.users.george = {
    isNormalUser = true;
    description = "George Kontridze";
    extraGroups = [ "libvirtd" "networkmanager" "wheel" ];
  };

  # List packages installed in system profile. To search, run:
  # $ nix search wget
  environment.systemPackages = with pkgs; [
    alacritty
    brave
    gnomeExtensions.systemd-manager
    google-chrome
    helix
    neovim
    nil
    nushell
    nyxt
    spotify
    systemdgenie
    virt-manager
    vscode
    xclip
    zellij
    (import
      (pkgs.fetchFromGitHub {
        owner = "vlinkz";
        repo = "nix-software-center";
        rev = "0.1.2";
        sha256 = "xiqF1mP8wFubdsAQ1BmfjzCgOD3YZf7EGWl9i69FTls=";
      })
      { })
  ];

  # This value determines the NixOS release from which the default
  # settings for stateful data, like file locations and database versions
  # on your system were taken. It‘s perfectly fine and recommended to leave
  # this value at the release version of the first install of this system.
  # Before changing this value read the documentation for this option
  # (e.g. man configuration.nix or on https://nixos.org/nixos/options.html).
  stateVersion = lib.removeSuffix "\n" (builtins.readFile ../../NIXOS_VERSION);
}
