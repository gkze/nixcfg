{
  lib,
  src,
  inputs,
  hostname,
  modulesPath,
  pkgs,
  system,
  ...
}:
{
  imports = [ "${modulesPath}/installer/cd-dvd/iso-image.nix" ];

  isoImage = {
    makeEfiBootable = true;
    makeUsbBootable = true;
  };

  networking.hostName = hostname;

  nix =
    let
      flakeInputs = lib.filterAttrs (_: lib.isType "flake") inputs;
    in
    {
      channel.enable = false;
      nixPath = lib.mapAttrsToList (n: _: "${n}=flake:${n}") flakeInputs;
      registry = lib.mapAttrs (_: flake: { inherit flake; }) flakeInputs;
      settings.experimental-features = [
        "flakes"
        "nix-command"
      ];
    };

  nixpkgs = {
    hostPlatform = system;
    config = {
      allowFree = true;
      allowInsecure = false;
    };
  };

  boot = {
    binfmt.registrations.appimage = {
      wrapInterpreterInShell = false;
      interpreter = "${pkgs.appimage-run}/bin/appimage-run";
      recognitionType = "magic";
      offset = 0;
      mask = "\\xff\\xff\\xff\\xff\\x00\\x00\\x00\\x00\\xff\\xff\\xff";
      magicOrExtension = "\\x7fELF....AI\\x02";
    };
  };

  documentation = {
    doc.enable = true;
    info.enable = true;
    man.enable = true;
  };

  environment.systemPackages = with pkgs; [
    awscli
    curl
    dasel
    fd
    file
    gawk
    git
    glab
    gpg
    gnused
    gnutar
    jq
    less
    moreutils
    neovim
    nh
    ripgrep
    rsync
    slack
    ssh
    trdsql
    tmux
    wl-clipboard
  ];

  programs = {
    man = {
      enable = true;
      generateCaches = true;
    };
  };

  time.timeZone = "America/Los_Angeles";

  services.xserver = {
    enable = true;
    displayManager.gdm.enable = true;
  };

  system.stateVersion = builtins.readFile "${src}/NIXOS_VERSION";

  users.users.root = {
    isSystemUser = true;
    # initialPassword = "root";
  };
}
