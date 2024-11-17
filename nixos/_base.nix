{
  lib,
  src,
  inputs,
  hostName,
  modulesPath,
  ...
}:
{
  imports = [ "${modulesPath}/virtualisation/qemu-vm.nix" ];

  networking.hostName = hostName;

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

  time.timeZone = "America/Los_Angeles";

  services.xserver = {
    enable = true;
    displayManager.gdm.enable = true;
  };

  system.stateVersion = builtins.readFile "${src}/NIXOS_VERSION";

  users.users.root = {
    isSystemUser = true;
    initialPassword = "root";
  };

  virtualisation.qemu.options = [
    "-device virtio-vga"
    "-m 2048"
  ];
}
