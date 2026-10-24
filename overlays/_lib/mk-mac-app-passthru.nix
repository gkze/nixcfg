# Shared `passthru.macApp` shape for managed macOS app bundles, exposed to
# packages and overlays as the `mkMacAppPassthru` helper so the contract
# consumed by lib/mac-apps.nix has exactly one definition.
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
