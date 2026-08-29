{
  mkUv2nixPackage,
  inputs,
  ...
}:
mkUv2nixPackage {
  name = "nix-manipulator";
  src = inputs.nix-manipulator;
  uvLockFile = ./uv.lock;
  mainProgram = "nima";
}
