{
  selfSource,
  slib,
  prev,
  ...
}:
let
  inherit (selfSource) version;
  src = prev.fetchFromGitHub {
    owner = "tursodatabase";
    repo = "turso-cli";
    tag = "v${version}";
    fetchSubmodules = false;
    hash = slib.sourceHash "turso-cli" "srcHash";
  };
in
{
  turso-cli = prev.turso-cli.overrideAttrs (_: {
    inherit version src;
    vendorHash = slib.sourceHash "turso-cli" "vendorHash";
  });
}
