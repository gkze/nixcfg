{
  mkGoCliPackage,
  inputs,
  lib,
  ...
}:
let
  mkGoCli = import ../../lib/go_cli_package.nix {
    inherit
      mkGoCliPackage
      inputs
      lib
      ;
  };
in
mkGoCli {
  pname = "gogcli";
  inputName = "gogcli";
  subPackage = "cmd/gog";
  cmdName = "gog";
  description = "Google Suite CLI: Gmail, GCal, GDrive, GContacts";
  homepage = "https://github.com/steipete/gogcli";
}
