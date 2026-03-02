{
  mkGoCliPackage,
  inputs,
  lib,
  ...
}:
import ../lib/go-cli-package.nix {
  inherit mkGoCliPackage inputs lib;
}
