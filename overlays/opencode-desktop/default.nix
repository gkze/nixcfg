{
  final,
  inputs,
  outputs,
  ...
}:
{
  opencode-desktop = final.callPackage ../../packages/opencode-desktop {
    inherit inputs outputs;
    inherit (final) nixcfgElectron opencode;
  };
}
