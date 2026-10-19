{
  final,
  inputs,
  outputs,
  ...
}:
{
  gitbutler = final.callPackage ../../packages/gitbutler {
    inherit inputs outputs;
    pkgs = final;
  };
}
