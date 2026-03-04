{
  src ? ../.,
}:
let
  exports = import ../lib/exports.nix { inherit src; };
in
{
  inherit (exports) darwinModules homeModules nixosModules;
}
