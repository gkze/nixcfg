{
  mkGoCliPackage,
  inputs,
  lib,
  ...
}:
let
  mkGoCli = import ../../lib/go-cli-package.nix {
    inherit
      mkGoCliPackage
      inputs
      lib
      ;
  };
in
mkGoCli {
  pname = "axiom-cli";
  inputName = "axiom-cli";
  subPackage = "cmd/axiom";
  cmdName = "axiom";
  description = "The power of Axiom on the command line";
  homepage = "https://github.com/axiomhq/cli";
}
