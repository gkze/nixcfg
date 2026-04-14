# Placeholder for NixOS work profile options.
# The work profile is currently Darwin-only; this stub ensures the module system
# loads cleanly on NixOS hosts.
{
  ...
}:
let
  workProfileSkeleton = import ../_profiles-work-skeleton.nix {
    enableDescription = "work profile (no-op on NixOS — see modules/darwin/profiles.nix)";
  };
in
{
  imports = [ workProfileSkeleton ];
}
