{
  mkUv2nixPackage,
  inputs,
  outputs,
  ...
}:
let
  slib = outputs.lib;
in
mkUv2nixPackage {
  name = "nix-manipulator";
  src = inputs.nix-manipulator;
  mainProgram = "nima";
  extraBuildPhase = ''
    export SETUPTOOLS_SCM_PRETEND_VERSION=${slib.getFlakeVersion "nix-manipulator"}
  '';
}
