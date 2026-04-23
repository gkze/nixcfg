{
  enableDescription ? "work profile",
}:
{ lib, ... }:
{
  imports = [
    (lib.mkAliasOptionModule [ "nixcfg" "profiles" "work" ] [ "profiles" "work" ])
  ];

  options.profiles.work.enable = lib.mkEnableOption enableDescription;
}
