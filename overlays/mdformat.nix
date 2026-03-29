{
  prev,
  slib,
  sources,
  ...
}:
{
  mdformat = prev.mdformat.override {
    python3 = prev.python3.override {
      packageOverrides = _: pyPrev: {
        mdformat = pyPrev.mdformat.overridePythonAttrs (
          _:
          let
            inherit (sources.mdformat) version;
          in
          {
            inherit version;
            src = prev.fetchFromGitHub {
              owner = "hukkin";
              repo = "mdformat";
              tag = version;
              hash = slib.sourceHash "mdformat" "srcHash";
            };
          }
        );
      };
    };
  };
}
