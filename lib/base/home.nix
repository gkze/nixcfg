{ src, lib, ... }:
{
  home.stateVersion = lib.removeSuffix "\n" (builtins.readFile "${src}/NIXOS_VERSION");
}
