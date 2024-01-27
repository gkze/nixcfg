{ pkgs, lib, inputs, hostPlatform, ... }:
let
  inherit (builtins) readFile;
  inherit (lib) removeSuffix;
in
{
  networking = {
    hostName = "mesa";
    networkmanager = { enable = true; wifi.backend = "iwd"; };
  };

  time.timeZone = "America/Los_Angeles";

  i18n = {
    inputMethod.enabled = "ibus";
    defaultLocale = "en_US.UTF-8";
    extraLocaleSettings = {
      LC_ADDRESS = "en_US.UTF-8";
      LC_IDENTIFICATION = "en_US.UTF-8";
      LC_MEASUREMENT = "en_US.UTF-8";
      LC_MONETARY = "en_US.UTF-8";
      LC_NAME = "en_US.UTF-8";
      LC_NUMERIC = "en_US.UTF-8";
      LC_PAPER = "en_US.UTF-8";
      LC_TELEPHONE = "en_US.UTF-8";
      LC_TIME = "en_US.UTF-8";
    };
  };

  programs = {
    # TODO: get working
    # programs.hyprland.enable = true;
    # https://github.com/Mic92/nix-ld
    nix-ld.enable = true;
    virt-manager.enable = true;
  };

  virtualisation = {
    docker.enable = true;
    libvirtd.enable = true;
  };

  # Linux application distribution format
  services.flatpak.enable = true;

  # Display
  services.xserver = {
    enable = true;
    desktopManager.gnome.enable = true;
    displayManager.gdm.enable = true;
    layout = "us";
    # When changing, run:
    # ```
    # $ gsettings reset org.gnome.desktop.input-sources xkb-option
    # $ gsettings reset org.gnome.desktop.input-sources sources
    # ```
    xkbOptions = "caps:swapescape";
  };

  # Sound
  sound.enable = true;
  hardware.pulseaudio.enable = false;
  security = { rtkit.enable = true; audit.enable = true; };
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
  };

  environment.systemPackages = with inputs; [
    # Nix software management GUI
    nix-software-center.packages.${hostPlatform}.default
    # Nix configuration editor GUI
    nixos-conf-editor.packages.${hostPlatform}.default
  ];

  # Define a user account. Don't forget to set a password with ‘passwd’.
  # TODO: factor out into separate system-agnostic (hopefully) user config
  users = {
    # Inter-Integrated Circuit (I2C)
    # https://en.wikipedia.org/wiki/I%C2%B2C
    # Used for communicating with external monitor(s) over DDC (Display Data
    # Channel)
    # Currently used to set brightness
    groups.i2c = { };
    users.george = {
      description = "George Kontridze";
      extraGroups = [ "docker" "i2c" "networkmanager" "wheel" ];
      isNormalUser = true;
      shell = pkgs.zsh;
    };
  };

  # This value determines the NixOS release from which the default
  # settings for stateful data, like file locations and database versions
  # on your system were taken. It‘s perfectly fine and recommended to leave
  # this value at the release version of the first install of this system.
  # Before changing this value read the documentation for this option
  # (e.g. man configuration.nix or on https://nixos.org/nixos/options.html).
  system.stateVersion = removeSuffix "\n" (readFile ../NIXOS_VERSION); # Did you read the comment?
}
