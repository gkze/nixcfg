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
# Unsigned object URLs 403. HEAD the public URL, fail closed unless the
# header/Location still name the pin, then GET that signed Location so
# the second hop cannot race to a newer latest.
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
    found_version=$(tr -d '\r' < headers | awk 'BEGIN{IGNORECASE=1} /^x-ara-desktop-version:/ {print $2; exit}')
    location=$(tr -d '\r' < headers | awk 'BEGIN{IGNORECASE=1} /^location:/ {print $2; exit}')
    if [ "$found_version" != "$version" ]; then
      echo "Reason download is $found_version, expected pinned $version" >&2
      echo "public redirect is latest-only; Update must re-pin the new version" >&2
      exit 1
    fi
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
