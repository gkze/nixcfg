{
  fetchurl,
  lib,
  selfSource,
  stdenvNoCC,
  system,
  ...
}:
let
  releaseAsset =
    {
      aarch64-darwin = "darwin-arm64";
      x86_64-darwin = "darwin-amd64";
    }
    .${system};
  sourceTarball = fetchurl {
    url = "https://github.com/tw93/Mole/archive/refs/tags/V${selfSource.version}.tar.gz";
    hash = "sha256-g+FH9DohdbTbJ2JBHjh5g2TBPpvV8jhohRhmLTAD/3I=";
  };
  binaryArchive = fetchurl {
    url = selfSource.urls.${system};
    hash = selfSource.hashes.${system};
  };
in
stdenvNoCC.mkDerivation {
  pname = "mole-app";
  inherit (selfSource) version;

  src = sourceTarball;

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/bin" "$out/libexec/mole"
    cp -R bin lib "$out/libexec/mole/"
    install -m755 mole "$out/bin/mole"
    substituteInPlace "$out/bin/mole" --replace-fail 'SCRIPT_DIR="$(cd "$(dirname "''${BASH_SOURCE[0]}")" && pwd)"' "SCRIPT_DIR='$out/libexec/mole'"

    tar -xzf ${binaryArchive} -C "$out/libexec/mole/bin"
    mv "$out/libexec/mole/bin/analyze-${releaseAsset}" "$out/libexec/mole/bin/analyze-go"
    mv "$out/libexec/mole/bin/status-${releaseAsset}" "$out/libexec/mole/bin/status-go"
    chmod +x "$out/libexec/mole/bin/analyze-go" "$out/libexec/mole/bin/status-go"
    ln -s "$out/bin/mole" "$out/bin/mo"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Deep clean and optimize your Mac";
    homepage = "https://github.com/tw93/Mole";
    license = licenses.mit;
    platforms = platforms.darwin;
    mainProgram = "mole";
  };
}
