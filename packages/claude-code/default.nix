{
  fetchurl,
  lib,
  makeBinaryWrapper,
  procps,
  ripgrep,
  selfSource,
  stdenvNoCC,
  system,
  ...
}:

stdenvNoCC.mkDerivation {
  pname = "claude-code";
  inherit (selfSource) version;

  src = fetchurl {
    url = selfSource.urls.${system};
    hash = selfSource.hashes.${system};
  };

  nativeBuildInputs = [ makeBinaryWrapper ];

  dontUnpack = true;
  dontBuild = true;
  dontStrip = true;

  installPhase = ''
    runHook preInstall

    install -Dm755 "$src" "$out/bin/claude"
    wrapProgram "$out/bin/claude" \
      --set DISABLE_AUTOUPDATER 1 \
      --set-default FORCE_AUTOUPDATE_PLUGINS 1 \
      --set DISABLE_INSTALLATION_CHECKS 1 \
      --set USE_BUILTIN_RIPGREP 0 \
      --prefix PATH : "${
        lib.makeBinPath [
          procps
          ripgrep
        ]
      }"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Agentic coding tool from Anthropic";
    homepage = "https://www.anthropic.com/claude-code";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "claude";
  };
}
