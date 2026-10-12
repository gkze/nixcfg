{
  cacert,
  curl,
  hash,
  lib,
  stdenvNoCC,
  url,
  version,
}:
# The public download is a latest-only 307 onto a versioned Tigris object
# (`/desktop/stable/<ver>/Reason.dmg`) whose signature expires in 900s.
# Unsigned object URLs 403. HEAD the public URL, fail closed unless
# Location still names /desktop/stable/<pin>/, then GET that signed
# Location so the second hop cannot race to a newer latest. Do not
# require x-ara-desktop-version: Update 38214877123 saw header 0.1.64
# while Location named 0.1.84. The object path is the pin. The FOD
# name must match AraUpdater.fod_name so prepare-darwin prefetch is
# substitutable (Update 38215834334 rebuilt after latest moved).
#
# Do not pass SSL_CERT_FILE through impureEnvVars. Hosted Darwin Nix
# injects /no-cert-file.crt there (Update 37406317879, curl 77 on the
# 0.1.73 FOD). Pin cacert like clearly/mux.
stdenvNoCC.mkDerivation {
  name = "Reason_${version}_aarch64.dmg";
  inherit url version;
  outputHash = hash;
  outputHashAlgo = "sha256";
  outputHashMode = "flat";
  preferLocalBuild = true;
  impureEnvVars = lib.fetchers.proxyImpureEnvVars;
  nativeBuildInputs = [
    cacert
    curl
  ];
  NIX_SSL_CERT_FILE = "${cacert}/etc/ssl/certs/ca-bundle.crt";
  SSL_CERT_FILE = "${cacert}/etc/ssl/certs/ca-bundle.crt";
  buildCommand = ''
    set -eu
    curl -fsSI --dump-header headers "$url" -o /dev/null
    location=$(tr -d '\r' < headers | awk 'BEGIN{IGNORECASE=1} /^location:/ {print $2; exit}')
    case "$location" in
      *"/desktop/stable/$version/"*) ;;
      *)
        echo "Reason Location is not the versioned object for $version:" >&2
        echo "$location" >&2
        exit 1
        ;;
    esac
    curl -fsSL "$location" -o "$out"
  '';
}
