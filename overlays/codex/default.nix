{
  final,
  inputs,
  outputs,
  ...
}:
{
  codex = final.callPackage ../../packages/codex {
    inherit inputs outputs;
    pkgs = final;
  };
}
