{
  buildNpmPackage,
  inputs,
  outputs,
  lib,
  ...
}:
let
  pname = "linearis";
  version = "unstable-${inputs.linearis.shortRev or (builtins.substring 0 8 inputs.linearis.rev)}";
  slib = outputs.lib;
in
buildNpmPackage {
  inherit pname version;
  src = inputs.linearis;
  npmDepsHash = slib.sourceHash pname "npmDepsHash";

  meta = {
    description = "CLI tool for Linear.app with JSON output and smart ID resolution";
    homepage = "https://github.com/czottmann/linearis";
    license = lib.licenses.mit;
    mainProgram = pname;
  };
}
