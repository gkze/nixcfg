{
  prev,
  slib,
  selfSource,
  ...
}:
{
  element-desktop = prev.element-desktop.overrideAttrs (_: rec {
    inherit (selfSource) version;
    src = prev.fetchFromGitHub {
      owner = "element-hq";
      repo = "element-web";
      tag = "v${version}";
      hash = slib.sourceHash "element-desktop" "srcHash";
    };
    pnpmDeps = prev.fetchPnpmDeps {
      pname = "element";
      inherit version src;
      fetcherVersion = 3;
      hash = slib.sourceHash "element-desktop" "sha256";
    };
  });
}
