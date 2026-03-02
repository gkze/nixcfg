{
  inputs,
  lib,
  outputs,
  pkgsFor,
  src,
  ...
}:
import ./lib.nix {
  inherit
    inputs
    lib
    outputs
    pkgsFor
    src
    ;
}
