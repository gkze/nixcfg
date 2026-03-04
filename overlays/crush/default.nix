{
  inputs,
  outputs,
  prev,
  ...
}:
let
  slib = outputs.lib;
  buildGoModule = prev.buildGoModule.override { go = prev.go_1_26; };
  crushWithGo126 = prev.crush.override { inherit buildGoModule; };
in
{
  crush = crushWithGo126.overrideAttrs (_: {
    version = slib.getFlakeVersion "crush";
    src = inputs.crush;
    vendorHash = slib.sourceHash "crush" "vendorHash";
    doCheck = false;
    ldflags = [
      "-s"
      "-X=github.com/charmbracelet/crush/internal/version.Version=${slib.getFlakeVersion "crush"}"
    ];
  });
}
