{
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "coast-local";
  bundleName = "Coast Local.app";
  info = selfSource;
  createBin = false;
  postInstallApp = ''
    coast_cli="$out/Applications/Coast Local.app/Contents/Resources/bin/coast"
    if [ ! -x "$coast_cli" ]; then
      echo "Expected Coast CLI in Coast Local.app" >&2
      exit 1
    fi
    ln -s "$coast_cli" "$out/bin/coast"
  '';
  description = "Always-on screen recorder with local inference and contextual search";
  homepage = "https://coast.app/";
  mainProgram = "coast";
  platforms = [ "aarch64-darwin" ];
}
