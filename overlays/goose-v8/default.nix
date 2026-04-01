{
  prev,
  slib,
  selfSource,
  ...
}:
{
  goose-v8 = prev.fetchgit {
    url = "https://github.com/jh-block/rusty_v8.git";
    rev = selfSource.version;
    fetchSubmodules = true;
    hash = slib.sourceHash "goose-v8" "srcHash";
  };
}
