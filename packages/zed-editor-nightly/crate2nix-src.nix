{
  callPackage,
  inputs,
  ...
}:
callPackage ./default.nix {
  inherit inputs;
  crate2nixSourceOnly = true;
}
