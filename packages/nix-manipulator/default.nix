{
  mkUv2nixPackage,
  inputs,
  ...
}:
mkUv2nixPackage {
  name = "nix-manipulator";
  src = inputs.nix-manipulator;
  mainProgram = "nima";
}
