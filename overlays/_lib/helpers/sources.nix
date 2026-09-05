{
  prev,
  sources,
  system,
  ...
}:
{
  mkSourceOverride =
    name: pkg:
    let
      info = sources.${name};
    in
    pkg.overrideAttrs {
      version = (info.pins or { }).${system} or info.version;
      src = prev.fetchurl {
        url = info.urls.${system} or (throw "sources.${name}.urls missing ${system}");
        hash = info.hashes.${system};
      };
    };
}
