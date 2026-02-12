{
  inputs,
  slib,
  prev,
  ...
}:
{
  crush =
    let
      version = slib.getFlakeVersion "crush";
    in
    prev.crush.overrideAttrs {
      inherit version;
      src = inputs.crush;
      vendorHash = slib.sourceHash "crush" "vendorHash";
    };
}
