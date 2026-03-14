{
  mkGoCli,
  ...
}:
mkGoCli {
  pname = "gogcli";
  inputName = "gogcli";
  subPackage = "cmd/gog";
  cmdName = "gog";
  description = "Google Suite CLI: Gmail, GCal, GDrive, GContacts";
  homepage = "https://github.com/steipete/gogcli";
}
