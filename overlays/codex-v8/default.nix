{
  prev,
  slib,
  selfSource,
  ...
}:
{
  codex-v8 = prev.fetchgit {
    url = "https://github.com/denoland/rusty_v8.git";
    rev = selfSource.commit;
    fetchSubmodules = true;
    hash = slib.sourceHash "codex-v8" "srcHash";
  };
}
