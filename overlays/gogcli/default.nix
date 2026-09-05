{ final, ... }:
{
  gogcli = final.mkGoCli {
    pname = "gogcli";
    # Intentional compatibility policy; the updater reads this choice through package passthru.
    go = final.go_1_27;
    cmdName = "gog";
    description = "Google Suite CLI: Gmail, GCal, GDrive, GContacts";
    homepage = "https://github.com/steipete/gogcli";
  };
}
