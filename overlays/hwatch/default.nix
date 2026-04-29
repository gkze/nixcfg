{
  inputs,
  slib,
  prev,
  ...
}:
let
  src = inputs.hwatch;
  version = slib.getFlakeVersion "hwatch";
  cargoHash = slib.sourceHash "hwatch" "cargoHash";
in
{
  hwatch = prev.hwatch.overrideAttrs (_: {
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
