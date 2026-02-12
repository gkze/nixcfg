{
  mkGoCliPackage,
  inputs,
  lib,
  ...
}:
mkGoCliPackage {
  pname = "axiom-cli";
  input = inputs.axiom-cli;
  subPackages = [ "cmd/axiom" ];
  cmdName = "axiom";
  meta = with lib; {
    description = "The power of Axiom on the command line";
    homepage = "https://github.com/axiomhq/cli";
    license = licenses.mit;
  };
}
