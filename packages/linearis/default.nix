{
  buildNpmPackage,
  inputs,
  outputs,
  lib,
  ...
}:
buildNpmPackage rec {
  pname = "linearis";
  version = "unstable-${inputs.linearis.shortRev or (builtins.substring 0 8 inputs.linearis.rev)}";
  src = inputs.linearis;
  npmDepsHash = outputs.lib.sourceHash pname "npmDepsHash";

  meta = {
    description = "CLI tool for Linear.app with JSON output and smart ID resolution";
    homepage = "https://github.com/czottmann/linearis";
    license = lib.licenses.mit;
    mainProgram = pname;
  };
}
