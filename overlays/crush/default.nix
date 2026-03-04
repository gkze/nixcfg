{
  sources,
  slib,
  prev,
  ...
}:
let
  buildGoModule = prev.buildGoModule.override { go = prev.go_1_26; };
  crushWithGo126 = prev.crush.override { inherit buildGoModule; };
  inherit (sources.crush) version;
  src = prev.fetchFromGitHub {
    owner = "charmbracelet";
    repo = "crush";
    tag = "v${version}";
    hash = slib.sourceHash "crush" "srcHash";
  };
in
{
  crush = crushWithGo126.overrideAttrs (_: {
    inherit version src;
    vendorHash = slib.sourceHash "crush" "vendorHash";
    doCheck = false;
    ldflags = [
      "-s"
      "-X=github.com/charmbracelet/crush/internal/version.Version=${version}"
    ];
  });
}
