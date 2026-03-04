{
  prev,
  slib,
  sources,
  ...
}:
let
  inherit (sources.goose-v8) version;
in
{
  goose-v8 = prev.fetchgit {
    url = "https://github.com/jh-block/rusty_v8.git";
    rev = version;
    fetchSubmodules = true;
    hash = slib.sourceHash "goose-v8" "srcHash";
  };
}
