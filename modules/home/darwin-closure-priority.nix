{ lib, ... }:
{
  # Keep Darwin host closures focused on the packages we actually want to
  # prioritize for update certification and cache warming. Standalone Home
  # Manager configs can still opt into the broader package sets.
  nixcfg.packageSets = {
    heavyOptional.enable = lib.mkDefault false;
    cloud.enable = lib.mkDefault false;
  };
}
