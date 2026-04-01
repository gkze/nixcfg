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
      repo = "element-desktop";
      rev = "v${version}";
      hash = slib.sourceHash "element-desktop" "srcHash";
    };
    offlineCache = prev.fetchYarnDeps {
      yarnLock = src + "/yarn.lock";
      hash = slib.sourceHash "element-desktop" "sha256";
    };
  });
}
