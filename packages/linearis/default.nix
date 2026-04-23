{
  fetchurl,
  makeWrapper,
  nodejs,
  outputs,
  lib,
  stdenvNoCC,
  ...
}:
let
  pname = "linearis";
  inherit ((outputs.lib.sourceEntry pname)) version;
  src = fetchurl {
    url = outputs.lib.sourceUrl pname "sha256";
    hash = outputs.lib.sourceHash pname "sha256";
  };
in
stdenvNoCC.mkDerivation {
  inherit pname version src;

  nativeBuildInputs = [ makeWrapper ];
  dontUnpack = true;

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/libexec/${pname}" "$out/bin"
    tar -xzf "$src" --strip-components=1 -C "$out/libexec/${pname}"
    makeWrapper ${lib.getExe nodejs} "$out/bin/${pname}" \
      --add-flags "$out/libexec/${pname}/dist/main.js"

    runHook postInstall
  '';

  meta = {
    description = "CLI tool for Linear.app with JSON output and smart ID resolution";
    homepage = "https://github.com/czottmann/linearis";
    license = lib.licenses.mit;
    mainProgram = pname;
    platforms = lib.platforms.unix;
  };
}
