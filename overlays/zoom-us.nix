{
  prev,
  selfSource,
  system,
  ...
}:
{
  zoom-us =
    if prev.stdenv.hostPlatform.isDarwin then
      prev.zoom-us.overrideAttrs (old: {
        inherit (selfSource) version;
        src = prev.fetchurl {
          url = selfSource.urls.${system};
          hash = selfSource.hashes.${system};
        };
        passthru = (old.passthru or { }) // {
          macApp = {
            bundleName = "zoom.us.app";
            bundleRelPath = "Applications/zoom.us.app";
            installMode = "copy";
          };
        };
      })
    else
      prev.zoom-us;
}
