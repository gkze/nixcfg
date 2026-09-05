{
  prev,
  slib,
  selfSource,
  ...
}:
let
  info = selfSource;
in
{
  mdformat = prev.mdformat.override {
    python3 = prev.python3.override {
      packageOverrides = _: pyPrev: {
        mdformat = pyPrev.mdformat.overridePythonAttrs (_: rec {
          inherit (info) version;
          src = prev.fetchFromGitHub {
            owner = "hukkin";
            repo = "mdformat";
            rev = info.commit;
            hash = slib.sourceHash "mdformat" "srcHash";
          };
        });
      };
    };
  };
}
