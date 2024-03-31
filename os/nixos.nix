{ pkgs, lib, users, ... }:
let
  inherit (builtins) listToAttrs readFile;
  inherit (lib) removeSuffix;
in
{
  nix = {
    distributedBuilds = true;

    buildMachines = [
      {
        hostName = "eu.nixbuild.net";
        system = "x86_64-linux";
        maxJobs = 100;
        supportedFeatures = [ "benchmark" "big-parallel" ];
      }
      # {
      #   hostName = "45.32.139.249";
      #   system = "x86_64-linux";
      #   maxJobs = 100;
      #   supportedFeatures = [ "benchmark" "big-parallel" ];
      # }
    ];

  };

  networking = {
    hostName = "mesa";
    networkmanager = { enable = true; wifi.backend = "iwd"; };
  };

  i18n = let locale = "en_US.UTF-8"; in {
    inputMethod.enabled = "ibus";
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

  programs = {
    # TODO: get working
    # programs.hyprland.enable = true;
    # https://github.com/Mic92/nix-ld
    nix-ld.enable = true;
    virt-manager.enable = true;
    ssh = {
      # TODO: improve
      extraConfig = ''
        Host eu.nixbuild.net
          PubkeyAcceptedKeyTypes ssh-ed25519
          ServerAliveInterval 60
          IPQoS throughput
          IdentityFile /home/george/.ssh/personal_ed25519_256.pem
          IdentityAgent /run/user/1000/keyring/ssh
        Host 45.32.139.249
          PubkeyAcceptedKeyTypes ssh-ed25519
          ServerAliveInterval 60
          IPQoS throughput
          IdentityFile /home/george/.ssh/personal_ed25519_256.pem
          IdentityAgent /run/user/1000/keyring/ssh
      '';
      knownHosts = {
        nixbuild = {
          hostNames = [ "eu.nixbuild.net" ];
          publicKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPIQCZc54poJ8vqawd8TraNryQeJnvH1eLpIDgbiqymM";
        };
        "45.32.139.249" = {
          hostNames = [ "45.32.139.249" ];
          publicKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOj6gF/E+yIBr30ieiejR/cqwaFEq/kn3BeRu41kwSlG";
        };
      };
    };
  };

  virtualisation = {
    docker.enable = true;
    libvirtd.enable = true;
  };

  services = {
    # Automatically set timezone
    automatic-timezoned.enable = true;
    # GNOME 3 enable keyring
    gnome.gnome-keyring.enable = true;
    # Application distribution format
    flatpak.enable = true;
    # Firmware UPdate Daemon
    fwupd.enable = true;
    # Display
    xserver = {
      enable = true;
      desktopManager.gnome.enable = true;
      displayManager.gdm = {
        enable = true;
        banner = "Go away";
      };
      # TODO: automate
      # When changing, run:
      # ```
      # $ gsettings reset org.gnome.desktop.input-sources xkb-option
      # $ gsettings reset org.gnome.desktop.input-sources sources
      # ```
      xkb = { options = "caps:swapescape"; layout = "us"; };
    };
    # Sound
    pipewire = {
      enable = true;
      alsa.enable = true;
      alsa.support32Bit = true;
      pulse.enable = true;
    };
  };


  # Sound
  sound.enable = true;
  hardware.pulseaudio.enable = false;
  security = {
    audit.enable = true;
    pam.services.login.enableGnomeKeyring = true;
    rtkit.enable = true;
  };

  environment.systemPackages = with pkgs; [
    # Nix software management GUI
    nix-software-center
    # Nix configuration editor GUI
    nixos-conf-editor
  ];

  # Define a user account. Don't forget to set a password with ‘passwd’.
  # TODO: factor out into separate system-agnostic (hopefully) user config
  users = {
    # Disallow imperatively managing users (via useradd / userdel etc.)
    # TODO: finish once secrets management is solved
    mutableUsers = true;
    # Inter-Integrated Circuit (I2C)
    # https://en.wikipedia.org/wiki/I%C2%B2C
    # Used for communicating with external monitor(s) over DDC (Display Data
    # Channel)
    # Currently used to set brightness
    groups.i2c = { };
    users = listToAttrs (map
      (u: {
        name = u;
        value = {
          description = u;
          extraGroups = [ "docker" "i2c" "networkmanager" "wheel" ];
          isNormalUser = true;
          shell = pkgs.zsh;
        };
      })
      users);
  };

  # This value determines the NixOS release from which the default
  # settings for stateful data, like file locations and database versions
  # on your system were taken. It‘s perfectly fine and recommended to leave
  # this value at the release version of the first install of this system.
  # Before changing this value read the documentation for this option
  # (e.g. man configuration.nix or on https://nixos.org/nixos/options.html).
  system.stateVersion = removeSuffix "\n" (readFile ../NIXOS_VERSION); # Did you read the comment?
}
