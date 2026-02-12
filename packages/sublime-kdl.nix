{
  stdenvNoCC,
  inputs,
  outputs,
  ...
}:
let
  slib = outputs.lib;
  flakeRef = slib.flakeLock.sublime-kdl;
in
stdenvNoCC.mkDerivation {
  pname = slib.normalizeName flakeRef.original.repo;
  version = flakeRef.original.ref;
  src = inputs.sublime-kdl;
  installPhase = "cp -r $src $out";
}
