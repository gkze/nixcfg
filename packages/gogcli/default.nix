{
  mkGoCliPackage,
  inputs,
  lib,
  ...
}:
mkGoCliPackage {
  pname = "gogcli";
  input = inputs.gogcli;
  subPackages = [ "cmd/gog" ];
  cmdName = "gog";
  meta = with lib; {
    description = "Google Suite CLI: Gmail, GCal, GDrive, GContacts";
    homepage = "https://github.com/steipete/gogcli";
    license = licenses.mit;
  };
}
