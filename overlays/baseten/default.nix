{
  final,
  outputs,
  selfSource,
  ...
}:
{
  baseten = final.callPackage ../../packages/baseten {
    inherit outputs selfSource;
  };
}
