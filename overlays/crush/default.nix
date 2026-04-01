{
  selfSource,
  slib,
  prev,
  ...
}:
let
  buildGoModule = prev.buildGoModule.override { go = prev.go_latest or prev.go; };
  crushOverrideArgs =
    if prev.crush ? override && prev.crush.override ? __functionArgs then
      prev.crush.override.__functionArgs
    else
      { };
  crushBase =
    if crushOverrideArgs ? buildGoModule then
      prev.crush.override { inherit buildGoModule; }
    else
      prev.crush;
  inherit (selfSource) version;
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
