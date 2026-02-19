# Placeholder for NixOS work profile options.
# The work profile is currently Darwin-only; this stub ensures the module system
# loads cleanly on NixOS hosts.
{ lib, ... }:
{
  options.profiles.work = {
    enable = lib.mkEnableOption "work profile (no-op on NixOS — see modules/darwin/profiles.nix)";
  };
}
