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
    repo = "turso";
    tag = "v${version}";
    fetchSubmodules = false;
    hash = slib.sourceHash "turso" "srcHash";
  };
  cargoHash = slib.sourceHash "turso" "cargoHash";
in
{
  turso = prev.turso.overrideAttrs (_: {
    inherit
      version
      src
      cargoHash
      ;
    cargoDeps = prev.rustPlatform.fetchCargoVendor {
      inherit src;
      hash = cargoHash;
    };
  });
}
