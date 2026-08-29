{ final, ... }:
{
  gogcli = final.mkGoCli {
    pname = "gogcli";
    go = final.go_1_27;
    cmdName = "gog";
    description = "Google Suite CLI: Gmail, GCal, GDrive, GContacts";
    homepage = "https://github.com/steipete/gogcli";
  };
}
