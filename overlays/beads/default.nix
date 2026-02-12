{
  inputs,
  final,
  prev,
  ...
}:
{
  beads = final.mkGoCliPackage {
    pname = "beads";
    input = inputs.beads;
    subPackages = [ "cmd/bd" ];
    cmdName = "bd";
    version = "0.0.0"; # beads doesn't have version tags
    proxyVendor = true;
    # beads requires Go >= 1.25.6, nixpkgs default is 1.25.5
    go = prev.go_1_26;
    # go-icu-regex (transitive dep via dolt) requires ICU headers
    buildInputs = [ prev.icu ];
  };
}
