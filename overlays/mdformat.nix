{
  prev,
  slib,
  selfSource ? null,
  sources,
  ...
}:
let
  info = if selfSource != null then selfSource else sources.mdformat;
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
            tag = version;
            hash = slib.sourceHash "mdformat" "srcHash";
          };
        });
      };
    };
  };
}
