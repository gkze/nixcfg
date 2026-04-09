{
  buildNpmPackage,
  inputs,
  outputs,
  lib,
  stdenv,
  ...
}:
let
  pname = "linearis";
  slib = outputs.lib;
  inherit (stdenv.hostPlatform) system;
  npmDepsHash =
    let
      perPlatformHash = builtins.tryEval (slib.sourceHashForPlatform pname "npmDepsHash" system);
    in
    if perPlatformHash.success then perPlatformHash.value else slib.sourceHash pname "npmDepsHash";
in
buildNpmPackage rec {
  inherit pname npmDepsHash;

  version = "unstable-${inputs.linearis.shortRev or (builtins.substring 0 8 inputs.linearis.rev)}";
  src = inputs.linearis;

  meta = {
    description = "CLI tool for Linear.app with JSON output and smart ID resolution";
    homepage = "https://github.com/czottmann/linearis";
    license = lib.licenses.mit;
    mainProgram = pname;
  };
}
