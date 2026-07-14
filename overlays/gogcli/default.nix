{ final, ... }:
{
  gogcli = final.mkGoCli {
    pname = "gogcli";
    cmdName = "gog";
    description = "Google Suite CLI: Gmail, GCal, GDrive, GContacts";
    homepage = "https://github.com/steipete/gogcli";
    postPatch = final.lib.optionalString (final.lib.versionOlder final.go.version "1.26.5") ''
      # Upstream only raised the patch-level minimum; keep this buildable until nixpkgs ships it.
      GOTOOLCHAIN=local go mod edit -go=${final.go.version}
    '';
  };
}
