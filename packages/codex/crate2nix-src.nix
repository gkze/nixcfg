{
  callPackage,
  inputs,
  outputs,
  ...
}:
callPackage ./default.nix {
  inherit inputs outputs;
  crate2nixSourceOnly = true;
}
