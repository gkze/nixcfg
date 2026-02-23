{
  inputs,
  slib,
  final,
  prev,
  ...
}:
{
  crush = final.mkGoCliPackage {
    pname = "crush";
    input = inputs.crush;
    subPackages = [ "." ];
    cmdName = "crush";
    version = slib.getFlakeVersion "crush";
    vendorHash = slib.sourceHash "crush" "vendorHash";
    go = prev.go_1_26;
    doCheck = false;
  };
}
