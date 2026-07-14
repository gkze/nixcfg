# Shared `passthru.macApp` shape for managed macOS app bundles. Used by the
# darwin-apps helpers and by `withManagedMacApp` in overlays/default.nix so
# the contract consumed by lib/mac-apps.nix has exactly one definition.
{
  bundleName,
  macApp ? { },
}:
{
  macApp = {
    inherit bundleName;
    bundleRelPath = "Applications/${bundleName}";
    installMode = "copy";
  }
  // macApp;
}
