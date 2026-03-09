{
  sources,
  slib,
  prev,
  ...
}:
let
  buildGoModule = prev.buildGoModule.override { go = prev.go_1_26; };
  crushBase =
    let
      maybeCrushWithGo126 = builtins.tryEval (prev.crush.override { inherit buildGoModule; });
    in
    if maybeCrushWithGo126.success then maybeCrushWithGo126.value else prev.crush;
  inherit (sources.crush) version;
  src = prev.fetchFromGitHub {
    owner = "charmbracelet";
    repo = "crush";
    tag = "v${version}";
    hash = slib.sourceHash "crush" "srcHash";
  };
in
{
  crush = crushBase.overrideAttrs (_: {
    inherit version src;
    vendorHash = slib.sourceHash "crush" "vendorHash";
    doCheck = false;
    ldflags = [
      "-s"
      "-X=github.com/charmbracelet/crush/internal/version.Version=${version}"
    ];
  });
}
