# Render Nix daemon settings as a `nix.conf` fragment.
#
# nix-darwin writes `nix.settings` into /etc/nix/nix.conf on hosts where it
# owns the installation. A host that lets Determinate Nix own Nix instead still
# needs the repository's cache, trust, and store-headroom policy to reach the
# running daemon, and Determinate's supported extension point is
# /etc/nix/nix.custom.conf. Encode values the way nix.conf expects: lists are
# space separated, booleans are true/false, and null values are omitted.
{ lib }:
let
  renderValue =
    value:
    if builtins.isBool value then
      lib.boolToString value
    else if builtins.isInt value then
      toString value
    else if builtins.isList value then
      lib.concatMapStringsSep " " renderValue value
    else
      toString value;
in
{
  inherit renderValue;

  render =
    settings:
    lib.concatStringsSep "\n" (
      lib.mapAttrsToList (name: value: "${name} = ${renderValue value}") (
        lib.filterAttrs (_: value: value != null) settings
      )
    )
    + "\n";
}
