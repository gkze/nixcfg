{
  go,
  lib,
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
  postPatch = lib.optionalString (lib.versionOlder go.version "1.26.2") ''
    # Upstream only raised the patch-level minimum; keep this buildable until nixpkgs ships it.
    substituteInPlace go.mod --replace-fail "go 1.26.2" "go ${go.version}"
  '';
}
